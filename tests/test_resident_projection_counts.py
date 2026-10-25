#!/usr/bin/env python3
"""resident wake/arrival 读面计数：一个 wake 一次，一段到达一次。

fastday-e 的 resident summary 写着 ``counters.wakes=327``、``journey_arrivals=292``，
而 ``autonomous_wakes.jsonl`` 只有 17 条记录、真实旅程只有 3 段已确认。差额不是生活量，
是每个 tick 把同一条投影再读一遍。本模块钉住修正后的口径：

1. 连续静默（``NOT_DUE``/``QUIET``）只计 journal 真正写下的第一条；
2. 同一时间戳的不同事件仍各计一次，不按 ``at`` 去重；
3. 完成旅程投影读 100 遍只计一次段到达；同旅程两段计 2，另一旅程再计 1；
4. 缺/坏身份不编计数；合成 tick dict（没走 runner 写盘）不算 wake；
5. 实时 ``_accumulate`` 与重启后的 ``_replay_journal`` 得到同一组数字；
6. 计数只读记录：不改 tick 的调用轨迹（settle/runner/reconcile 次数不变），
   不触发 World realize、不写 owner。

夹具全部在 /tmp，测试不需要模型、网络或真实等待。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import autonomous_life_assembly as assembly  # noqa: E402
import autonomous_life_resident as resident  # noqa: E402
import autonomous_life_runner as runner_module  # noqa: E402
import resident_projection_counts as projection_counts  # noqa: E402

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
AT = NOW.isoformat(timespec="seconds")


def _wake(*, status: str, status_class: str, outcome=None, kind: str = "CHECK_WINDOW",
          at: str = AT, llm_calls: int = 0, extra=None) -> dict:
    return {"kind": kind, "reason": "check-window", "status": status,
            "status_class": status_class, "at": at, "trigger_ref": "",
            "trigger_kind": "", "llm_calls": llm_calls, "detail": "",
            "outcome": outcome, "decision": None, "activity_ref": None,
            "action_id": None, "action_status": None, "extra": dict(extra or {})}


def _journey(*, journey_id="journey:one", confirmed=1, arrived=True, refused=None) -> dict:
    record = {"ok": True, "journey_id": journey_id, "segments_confirmed": confirmed,
              "arrived": bool(arrived), "journey_status": "completed"}
    if refused:
        record["arrival_refused"] = refused
    return record


def _tick(*, journey=None, due=None, events=None, tick=1) -> dict:
    return {"at": AT, "logical_at": None, "tick": tick,
            "events": list(events or []), "feed_errors": [],
            "due": due or _wake(status="NOT_DUE", status_class="silence"),
            "reconciled": [], "journey": journey}


def _state_root(prefix: str = "v04-projection-counts-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def _resident(root: Path) -> "resident.AutonomousLifeResident":
    return resident.AutonomousLifeResident(
        state_root=root, environment={}, poll_seconds=180,
        clock=lambda: NOW, sleeper=lambda seconds: None, monotonic=lambda: 0.0)


class ProjectionCounterTest(unittest.TestCase):
    """两条规则本身，不经过 resident。"""

    def test_repeated_completed_projection_is_one_arrival(self):
        counter = projection_counts.ProjectionCounter()
        record = _journey(confirmed=1)
        hits = [counter.journey_delta(record) for _ in range(100)]
        self.assertEqual(sum(item[0] for item in hits), 1)

    def test_two_segments_of_one_journey_and_another_journey(self):
        counter = projection_counts.ProjectionCounter()
        self.assertEqual(counter.journey_delta(_journey(confirmed=1))[0], 1)
        self.assertEqual(counter.journey_delta(_journey(confirmed=2))[0], 1)
        self.assertEqual(counter.journey_delta(_journey(confirmed=2))[0], 0)
        self.assertEqual(
            counter.journey_delta(_journey(journey_id="journey:two", confirmed=1))[0], 1)

    def test_bad_or_missing_identity_is_not_counted(self):
        counter = projection_counts.ProjectionCounter()
        for record in (
            {"ok": True, "segments_confirmed": 1},
            {"ok": True, "journey_id": "", "segments_confirmed": 1},
            {"ok": True, "journey_id": "j", "segments_confirmed": True},
            {"ok": True, "journey_id": "j", "segments_confirmed": -1},
            {"ok": True, "journey_id": "j", "segments_confirmed": "2"},
            {"ok": False, "journey_id": "j", "segments_confirmed": 2},
            None,
        ):
            self.assertEqual(counter.journey_delta(record), (0, 0))

    def test_refusal_is_counted_once_per_owner_evidence(self):
        counter = projection_counts.ProjectionCounter()
        record = _journey(confirmed=2, arrived=False, refused="POSITION_MISMATCH")
        hits = [counter.journey_delta(record) for _ in range(50)]
        self.assertEqual(sum(item[1] for item in hits), 1)

    def test_repeated_quiet_projection_is_one_wake(self):
        counter = projection_counts.ProjectionCounter()
        quiet = _wake(status="NOT_DUE", status_class="silence")
        self.assertTrue(counter.wake_is_recorded(quiet))
        for _ in range(99):
            self.assertFalse(counter.wake_is_recorded(quiet))

    def test_a_new_wake_after_quiet_streak_counts_again(self):
        counter = projection_counts.ProjectionCounter()
        quiet = _wake(status="NOT_DUE", status_class="silence")
        acted = _wake(status="ACTED", status_class="acted", outcome="ACTED")
        counted = []
        for record in [quiet] * 5 + [acted] + [quiet] * 5:
            counted.append(counter.wake_is_recorded(record))
        self.assertEqual(counted.count(True), 3)
        self.assertTrue(counted[0])
        self.assertTrue(counted[5])
        self.assertTrue(counted[6])

    def test_quiet_and_not_due_share_the_same_streak(self):
        """runner 的 ``_journal_quiet`` 把 NOT_DUE 与 QUIET 视为同一种静默。"""

        counter = projection_counts.ProjectionCounter()
        self.assertTrue(counter.wake_is_recorded(
            _wake(status="NOT_DUE", status_class="silence")))
        self.assertFalse(counter.wake_is_recorded(
            _wake(status="QUIET", status_class="silence")))

    def test_same_timestamp_different_events_each_count(self):
        counter = projection_counts.ProjectionCounter()
        first = _wake(status="ACTED", status_class="acted", outcome="ACTED",
                      kind="EVENT", at=AT, llm_calls=1)
        second = _wake(status="NO_ACTION", status_class="choice", outcome="NO_ACTION",
                       kind="EVENT", at=AT)
        self.assertTrue(counter.wake_is_recorded(first))
        self.assertTrue(counter.wake_is_recorded(second))

    def test_synthetic_tick_dict_is_not_a_wake(self):
        counter = projection_counts.ProjectionCounter()
        synthetic = {"kind": "CHECK_WINDOW", "status": "CONTACT_DECISION_HANDLED",
                     "llm_calls": 0, "decision": None, "detail": "invitation handled"}
        self.assertFalse(counter.wake_is_recorded(synthetic))


class ResidentWiringTest(unittest.TestCase):
    """真实 resident 的 ``_accumulate`` / ``_replay_journal`` 接线。"""

    def test_repeated_projection_does_not_inflate_the_summary(self):
        root = _state_root()
        loop = _resident(root)
        record = _tick(journey=_journey(confirmed=2))
        for _ in range(100):
            loop._accumulate(record)
        self.assertEqual(loop.counters["wakes"], 1)
        self.assertEqual(loop.counters["journey_arrivals"], 2)
        self.assertEqual(loop.counters["journey_settlements"], 0)

        acted = _tick(due=_wake(status="ACTED", status_class="acted", outcome="ACTED",
                                llm_calls=1))
        loop._accumulate(acted)
        self.assertEqual(loop.counters["wakes"], 2)
        self.assertEqual(loop.counters["model_calls"], 1)
        self.assertEqual(loop.counters["actions"], 1)

    def test_replay_rebuilds_the_same_counts_after_restart(self):
        root = _state_root()
        first = _resident(root)
        for index in range(100):
            record = _tick(journey=_journey(confirmed=2), tick=index + 1)
            first._journal(record)
            first._accumulate(record)
        acted = _tick(due=_wake(status="ACTED", status_class="acted",
                                outcome="ACTED", llm_calls=1), tick=101)
        first._journal(acted)
        first._accumulate(acted)
        before = dict(first.counters)

        second = _resident(root)
        second._replay_journal()
        self.assertEqual(second.counters["wakes"], before["wakes"])
        self.assertEqual(second.counters["journey_arrivals"], before["journey_arrivals"])
        self.assertEqual(second.counters["model_calls"], before["model_calls"])
        self.assertEqual(second.ticks, 101)
        # 重启后同一条完成投影再读一次：不重计。
        second._accumulate(_tick(journey=_journey(confirmed=2), tick=102))
        self.assertEqual(second.counters["journey_arrivals"], 2)

    def test_rule_resolved_and_model_rounds_stay_apart(self):
        root = _state_root()
        loop = _resident(root)
        rule = _tick(due=_wake(status="NO_ACTION", status_class="choice",
                               outcome="NO_ACTION",
                               extra={"decision_route": "RULE_RESOLVED"}))
        loop._accumulate(rule)
        self.assertEqual(loop.counters["wakes"], 1)
        self.assertEqual(loop.counters["rule_resolved_windows"], 1)
        self.assertEqual(loop.counters["model_calls"], 0)

        model = _tick(due=_wake(status="NO_ACTION", status_class="choice",
                                outcome="NO_ACTION", llm_calls=1,
                                extra={"decision_route": "COGNITIVE_REQUIRED"}))
        loop._accumulate(model)
        self.assertEqual(loop.counters["wakes"], 2)
        self.assertEqual(loop.counters["rule_resolved_windows"], 1)
        self.assertEqual(loop.counters["model_calls"], 1)

    def test_counts_come_from_records_only_and_touch_no_owner(self):
        root = _state_root()
        loop = _resident(root)
        loop._accumulate(_tick(journey=_journey(confirmed=1)))
        self.assertIsNone(loop.runtime)
        self.assertIsNone(loop.assembled)
        self.assertIsNone(loop.last_error)


class _TrajectoryRunner:
    def __init__(self):
        self.reconcile_calls = 0
        self.pending_calls = 0

    def reconcile_pending(self):
        self.reconcile_calls += 1
        return []

    def pending_unknown_actions(self):
        self.pending_calls += 1
        return []


class _TrajectoryCycle:
    def __init__(self):
        self.settle_calls = 0

    def settle_world_journey(self, *, observed_at=None):
        self.settle_calls += 1
        return _journey(confirmed=2)


class TickTrajectoryTest(unittest.TestCase):
    """计数去重不能改变 tick 的调用轨迹。"""

    def test_tick_calls_are_the_same_with_or_without_duplicate_projections(self):
        root = _state_root()
        loop = _resident(root)
        runner = _TrajectoryRunner()
        cycle = _TrajectoryCycle()
        loop.assembled = {"state": assembly.READY, "runner": runner, "cycle": cycle}
        runner_tick_calls = []

        def _fake_runner_tick(assembled, *, act=True):
            runner_tick_calls.append(1)
            return {"ok": True, "waiting": None, "contacts": None, "events": [],
                    "feed_errors": [],
                    "due": _wake(status="NOT_DUE", status_class="silence")}

        original = assembly.runner_tick
        assembly.runner_tick = _fake_runner_tick
        try:
            for _ in range(3):
                loop.tick()
        finally:
            assembly.runner_tick = original

        # 每个 tick 恰好触发一次结算、一次 runner 回合、一次对账；计数去重没有增加或
        # 减少任何外部调用。
        self.assertEqual(cycle.settle_calls, 3)
        self.assertEqual(len(runner_tick_calls), 3)
        self.assertEqual(runner.reconcile_calls, 3)
        # 三条 NOT_DUE 投影只对应一条 journal wake。
        self.assertEqual(loop.counters["wakes"], 1)
        # 同一完成旅程投影三次，段到达仍是 2。
        self.assertEqual(loop.counters["journey_arrivals"], 2)
        self.assertEqual(loop.counters["journey_settlements"], 3)

    def test_legacy_per_projection_counting_has_the_same_trajectory(self):
        """同一输入，只换计数口径：调用轨迹必须逐步相同，只有 summary 数字不同。"""

        class _AlwaysCounted:
            """改前的口径：每条投影都当新的 wake/arrival。"""

            @staticmethod
            def wake_is_recorded(record):
                return projection_counts.is_wake_record(record)

            @staticmethod
            def journey_delta(record, *, before=None):
                return (0, 0)

        def _run(use_legacy: bool):
            root = _state_root()
            loop = _resident(root)
            runner = _TrajectoryRunner()
            cycle = _TrajectoryCycle()
            loop.assembled = {"state": assembly.READY, "runner": runner, "cycle": cycle}
            if use_legacy:
                loop._projections = _AlwaysCounted()
            timeline = []

            def _fake_runner_tick(assembled, *, act=True):
                timeline.append("runner_tick")
                return {"ok": True, "waiting": None, "contacts": None, "events": [],
                        "feed_errors": [],
                        "due": _wake(status="NOT_DUE", status_class="silence")}

            original = assembly.runner_tick
            assembly.runner_tick = _fake_runner_tick
            try:
                for _ in range(3):
                    loop.tick()
                    timeline.append(("settle", cycle.settle_calls,
                                     "reconcile", runner.reconcile_calls))
            finally:
                assembly.runner_tick = original
            return timeline, dict(loop.counters)

        legacy_timeline, legacy_counters = _run(True)
        fixed_timeline, fixed_counters = _run(False)
        self.assertEqual(legacy_timeline, fixed_timeline)
        self.assertEqual(legacy_counters["wakes"], 3)
        self.assertEqual(fixed_counters["wakes"], 1)


class JournalRecoveryTest(unittest.TestCase):
    """缓存淘汰后，计数依据必须回到既有 journal，而不是重新从 0 开始。"""

    def test_forty_journeys_then_repeat_the_evicted_one_is_not_recounted(self):
        """父级已复现的反例：40 个旅程各确认一次后重读 journey:0，额外到达应为 0。"""

        root = _state_root()
        loop = _resident(root)
        tick = 0
        for index in range(40):
            tick += 1
            record = _tick(journey=_journey(journey_id="journey:%d" % index, confirmed=1),
                           tick=tick)
            loop._journal(record)
            loop._accumulate(record)
        self.assertEqual(loop.counters["journey_arrivals"], 40)

        for _ in range(5):
            tick += 1
            repeat = _tick(journey=_journey(journey_id="journey:0", confirmed=1), tick=tick)
            loop._journal(repeat)
            loop._accumulate(repeat)
        self.assertEqual(loop.counters["journey_arrivals"], 40)

    def test_replay_recovers_evicted_journeys_from_the_same_journal(self):
        root = _state_root()
        first = _resident(root)
        tick = 0
        for index in range(40):
            tick += 1
            record = _tick(journey=_journey(journey_id="journey:%d" % index, confirmed=1),
                           tick=tick)
            first._journal(record)
            first._accumulate(record)
        # 再投影最早 8 个旅程：实时缓存只剩最近 32 个，旧实现会把这 8 条重算。
        for index in range(8):
            tick += 1
            repeat = _tick(journey=_journey(journey_id="journey:%d" % index, confirmed=1),
                           tick=tick)
            first._journal(repeat)
            first._accumulate(repeat)
        self.assertEqual(first.counters["journey_arrivals"], 40)

        second = _resident(root)
        second._replay_journal()
        self.assertEqual(second.counters["journey_arrivals"], 40)
        self.assertEqual(second.ticks, tick)
        # 重放之后同一条旧投影再读：仍不增。
        second._accumulate(_tick(journey=_journey(journey_id="journey:0", confirmed=1),
                                 tick=tick + 1))
        self.assertEqual(second.counters["journey_arrivals"], 40)

    def test_out_of_order_confirmed_never_lowers_the_watermark(self):
        root = _state_root()
        loop = _resident(root)
        tick = 0
        assignments = (1, 3, 1, 2, 4, 2)
        for confirmed in assignments:
            tick += 1
            record = _tick(journey=_journey(journey_id="journey:water",
                                            confirmed=confirmed), tick=tick)
            loop._journal(record)
            loop._accumulate(record)
        self.assertEqual(loop.counters["journey_arrivals"], 4)

        # 灌满缓存，把 journey:water 挤出去；历史高水位必须从 journal 恢复。
        for index in range(40):
            tick += 1
            record = _tick(journey=_journey(journey_id="journey:other-%d" % index,
                                            confirmed=1), tick=tick)
            loop._journal(record)
            loop._accumulate(record)
        self.assertEqual(loop.counters["journey_arrivals"], 44)

        for confirmed, expected_total in ((1, 44), (4, 44), (5, 45), (2, 45)):
            tick += 1
            record = _tick(journey=_journey(journey_id="journey:water",
                                            confirmed=confirmed), tick=tick)
            loop._journal(record)
            loop._accumulate(record)
            self.assertEqual(loop.counters["journey_arrivals"], expected_total)

    def test_refusal_keys_are_recovered_after_cache_eviction(self):
        root = _state_root()
        loop = _resident(root)
        tick = 0
        for index in range(70):
            tick += 1
            record = _tick(journey=_journey(journey_id="journey:refused-%d" % index,
                                            confirmed=1, arrived=False,
                                            refused="REASON_%d" % index), tick=tick)
            loop._journal(record)
            loop._accumulate(record)
        self.assertEqual(loop.counters["journey_arrivals"], 70)
        self.assertEqual(loop.counters["journey_arrival_refusals"], 70)

        # 最早的拒绝键与旅程都已被淘汰；持久行还在，重读不能再计。
        for index in (0, 1, 69):
            tick += 1
            repeat = _tick(journey=_journey(journey_id="journey:refused-%d" % index,
                                            confirmed=1, arrived=False,
                                            refused="REASON_%d" % index), tick=tick)
            loop._journal(repeat)
            loop._accumulate(repeat)
        self.assertEqual(loop.counters["journey_arrival_refusals"], 70)
        self.assertEqual(loop.counters["journey_arrivals"], 70)


def _runner_with_log(path: Path) -> "runner_module.AutonomousLifeRunner":
    runner = runner_module.AutonomousLifeRunner.__new__(runner_module.AutonomousLifeRunner)
    runner.last_wake = None
    runner.wakes_run = 0
    runner.last_error = None
    runner._journal_quiet = False
    runner.wake_log = path
    return runner


class RunnerJournalFailureTest(unittest.TestCase):
    """写盘失败时 runner 不抛，但计数面不能把没落盘的记录算成 wake。"""

    def test_failed_record_write_does_not_add_a_wake(self):
        root = _state_root()
        blocked = root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        runner = _runner_with_log(blocked / "autonomous_wakes.jsonl")

        wake = runner._record(runner_module.WAKE_KIND_CHECK_WINDOW, "check-window",
                              "ACTED", "acted", NOW, llm_calls=1, outcome="ACTED")
        self.assertTrue(wake.extra.get("journal_write_failed"))
        self.assertIsNotNone(runner.last_error)
        self.assertFalse((blocked / "autonomous_wakes.jsonl").exists())

        loop = _resident(root / "resident")
        loop._accumulate(_tick(due=wake.to_dict(), tick=1))
        self.assertEqual(loop.counters["wakes"], 0)
        # 请求真的发生过，model_calls 仍如实累计；只有 wake 不虚增。
        self.assertEqual(loop.counters["model_calls"], 1)

        runner.wake_log = root / "autonomous_wakes.jsonl"
        second = runner._record(runner_module.WAKE_KIND_CHECK_WINDOW, "check-window",
                                "ACTED", "acted", NOW, llm_calls=1, outcome="ACTED")
        self.assertNotIn("journal_write_failed", second.extra)
        self.assertTrue(runner.wake_log.exists())
        loop._accumulate(_tick(due=second.to_dict(), tick=2))
        self.assertEqual(loop.counters["wakes"], 1)
        self.assertEqual(loop.counters["model_calls"], 2)

    def test_failed_first_quiet_write_keeps_the_streak_suppressed(self):
        root = _state_root()
        blocked = root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        runner = _runner_with_log(blocked / "autonomous_wakes.jsonl")

        first = runner._record(runner_module.WAKE_KIND_CHECK_WINDOW, "check-window",
                               "NOT_DUE", "silence", NOW)
        self.assertTrue(first.extra.get("journal_write_failed"))
        loop = _resident(root / "resident")
        loop._accumulate(_tick(due=first.to_dict(), tick=1))
        self.assertEqual(loop.counters["wakes"], 0)
        # runner 已把静默 streak 标记前进（不会重试这一条），下一拍静默同样没落盘。
        second = runner._record(runner_module.WAKE_KIND_CHECK_WINDOW, "check-window",
                                "NOT_DUE", "silence", NOW)
        loop._accumulate(_tick(due=second.to_dict(), tick=2))
        self.assertEqual(loop.counters["wakes"], 0)

    def test_synthetic_contact_decision_tick_is_not_a_wake(self):
        root = _state_root()
        loop = _resident(root)
        synthetic = {"kind": "CHECK_WINDOW", "status": "CONTACT_DECISION_HANDLED",
                     "llm_calls": 0, "decision": None,
                     "detail": "this tick already considered an explicit invitation"}
        loop._accumulate(_tick(due=synthetic, tick=1))
        self.assertEqual(loop.counters["wakes"], 0)


if __name__ == "__main__":
    unittest.main()
