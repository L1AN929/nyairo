#!/usr/bin/env python3
"""只读运行健康：安静、已停、旧记录残留与待处理积压必须是四种可区分的事实。

这里不启动进程、不调用模型，只用临时目录里的记录与伪 ``/proc`` 注入启动时间，核对：

1. 记录写到 running=true、但进程已不在 -> ``RECORD_STALE_PROCESS_ABSENT``，不是在线；
2. 正规停止（running=false）且进程不在 -> ``STOPPED``；
3. 一个 pid 现在属于启动时间不同的别的进程 -> ``NOT_RUNNING``/``PID_REUSED``，pid 存在不算数；
4. 跨主机或无记录身份 -> ``UNKNOWN``，不猜死活；
5. AR-0 有未确认提交意图 -> ``PENDING``，且**不**出现 STALLED；
6. 有明确失败结局但队列空 -> ``LAST_FAILURE``，与 IDLE 分开；
7. quiet 不设阈值：last_tick 很久以前，进程在仍是 ``ALIVE_QUIET``。

无 ``/proc`` 的平台跳过进程匹配用例（如实 UNKNOWN 的那一条仍会跑）。临时根保留。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import runtime_health_review as review  # noqa: E402

BASE = datetime(2026, 10, 29, 6, 0, 0, tzinfo=timezone.utc)
HOST = "test-host"
HERTZ = float(os.sysconf("SC_CLK_TCK")) if os.name == "posix" else 100.0


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _state_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="runtime-health-test-"))


def _write_summary(root: Path, **fields) -> None:
    payload = {"kind": "autonomous_life_resident", "pid": 424242,
               "started_at": _iso(BASE), "this_process_started_at": _iso(BASE),
               "running": True, "starts": 1, "ticks": 2,
               "last_tick_at": _iso(BASE + timedelta(seconds=2))}
    payload.update(fields)
    (root / "resident_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_coverage(root: Path, *, pid: int = 424242, started: datetime = BASE,
                    stopped: datetime | None = None, closed: str = "UNCLOSED") -> None:
    row = {"process_id": "%s:%s:%s" % (HOST, pid, _iso(started)), "pid": pid,
           "started_at": _iso(started), "last_heartbeat_at": _iso(stopped or started),
           "stopped_at": _iso(stopped) if stopped else None, "closed": closed,
           "online_seconds": None, "online_seconds_source": "UNKNOWN"}
    (root / "resident_coverage.json").write_text(
        json.dumps({"kind": "life_runtime_coverage", "schema_version": 1,
                    "state_root": str(root), "processes": [row]}),
        encoding="utf-8")


def _proc_root(root: Path, *, btime: float = 1000.0) -> Path:
    """An empty-but-readable ``/proc``: the host has procfs, this pid is simply not there."""

    proc = root / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "stat").write_text("cpu 0 0 0\nbtime %d\n" % int(btime), encoding="utf-8")
    return proc


def _fake_proc(root: Path, pid: int, start_epoch: float, *, btime: float = 1000.0) -> Path:
    proc = root / "proc"
    (proc / str(pid)).mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text("cpu 0 0 0\nbtime %d\n" % int(btime), encoding="utf-8")
    ticks = int(round((start_epoch - btime) * HERTZ))
    fields = ["0"] * 30
    fields[0] = "S"
    fields[19] = str(ticks)
    (proc / str(pid) / "stat").write_text("%d (python) %s\n" % (pid, " ".join(fields)),
                                          encoding="utf-8")
    return proc


def _write_queue(root: Path, state: dict) -> None:
    (root / "action_reality_state.json").write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")


class ClassificationTest(unittest.TestCase):
    def test_no_record_is_not_a_verdict_about_life(self):
        root = _state_root()
        result = review.health(root, hostname=HOST, proc_root=root / "proc")
        self.assertEqual(result["verdict"], review.VERDICT_NO_RECORD)
        self.assertIn("不编造", result["explain"])

    def test_old_running_claim_with_a_gone_process_is_stale_not_alive(self):
        root = _state_root()
        _write_summary(root, running=True)
        _write_coverage(root, closed="UNCLOSED")  # the process never closed its own row
        result = review.health(root, hostname=HOST, proc_root=_proc_root(root))
        self.assertEqual((result["process"])["status"], review.PROCESS_NOT_RUNNING)
        self.assertEqual(result["verdict"], review.VERDICT_RECORD_STALE)

    def test_a_stopped_record_and_a_gone_process_is_stopped(self):
        root = _state_root()
        _write_summary(root, running=False, stopped_at=_iso(BASE + timedelta(seconds=3)))
        _write_coverage(root, stopped=BASE + timedelta(seconds=3), closed="STOP")
        result = review.health(root, hostname=HOST, proc_root=_proc_root(root))
        self.assertEqual(result["verdict"], review.VERDICT_STOPPED)


class ProcessIdentityTest(unittest.TestCase):
    def test_matching_start_time_is_the_same_process(self):
        root = _state_root()
        epoch = 2000.0
        started = datetime.fromtimestamp(epoch, tz=timezone.utc)
        _write_summary(root, started_at=_iso(started), this_process_started_at=_iso(started))
        _write_coverage(root, started=started)
        proc = _fake_proc(root, 424242, epoch)
        result = review.health(root, hostname=HOST, proc_root=proc, now=started)
        self.assertEqual((result["process"])["status"], review.PROCESS_MATCHED)
        self.assertEqual(result["verdict"], review.VERDICT_ALIVE_QUIET)

    def test_a_reused_pid_is_not_the_recorded_process(self):
        root = _state_root()
        recorded = datetime.fromtimestamp(2000.0, tz=timezone.utc)
        _write_summary(root, started_at=_iso(recorded), this_process_started_at=_iso(recorded))
        _write_coverage(root, started=recorded)
        proc = _fake_proc(root, 424242, 9000.0)  # same pid, a different start instant
        result = review.health(root, hostname=HOST, proc_root=proc, now=recorded)
        self.assertEqual((result["process"])["status"], review.PROCESS_NOT_RUNNING)
        self.assertEqual((result["process"])["basis"], "PID_REUSED")
        self.assertEqual(result["verdict"], review.VERDICT_RECORD_STALE)

    def test_a_different_host_is_unknown_never_alive_or_dead(self):
        root = _state_root()
        _write_summary(root)
        _write_coverage(root)
        result = review.health(root, hostname="some-other-host", proc_root=root / "proc")
        self.assertEqual((result["process"])["status"], review.PROCESS_UNKNOWN)
        self.assertEqual((result["process"])["basis"], "RECORDED_HOST_DIFFERS")
        self.assertEqual(result["verdict"], review.VERDICT_UNKNOWN)

    def test_a_summary_pid_without_a_host_is_unknown(self):
        root = _state_root()
        _write_summary(root)  # summary only; no ledger row, so no host
        result = review.health(root, hostname=HOST, proc_root=root / "proc")
        self.assertEqual((result["process"])["basis"], "NO_RECORDED_HOST")
        self.assertEqual(result["verdict"], review.VERDICT_UNKNOWN)


class QueueTest(unittest.TestCase):
    def _live_root(self) -> tuple[Path, Path]:
        root = _state_root()
        epoch = BASE.timestamp()
        _write_summary(root, started_at=_iso(BASE), this_process_started_at=_iso(BASE))
        _write_coverage(root, started=BASE)
        return root, _fake_proc(root, 424242, epoch)

    def test_an_open_submission_intent_is_pending_not_stalled(self):
        root, proc = self._live_root()
        _write_queue(root, {"schema_version": "x", "revision": 4,
                            "open_submission_intents": {"actn:1": {"attempt_id": "aatt:1"}},
                            "actions": {"actn:1": {"status": "PREPARED"}},
                            "attempts": {}})
        result = review.health(root, hostname=HOST, proc_root=proc)
        self.assertEqual((result["queue"])["verdict"], review.QUEUE_PENDING)
        self.assertEqual(result["verdict"], review.VERDICT_ALIVE_PENDING)
        self.assertNotIn("STALLED", json.dumps(result, ensure_ascii=False))

    def test_a_definite_failure_with_an_empty_queue_is_shown_separately(self):
        root, proc = self._live_root()
        _write_queue(root, {"schema_version": "x", "revision": 2,
                            "open_submission_intents": {}, "actions": {},
                            "attempts": {"aatt:1": {"action_id": "actn:1",
                                                    "status": "PRE_SUBMIT_FAILED",
                                                    "error_class": "GATE_OFF",
                                                    "started_at": _iso(BASE)}}})
        result = review.health(root, hostname=HOST, proc_root=proc)
        self.assertEqual((result["queue"])["verdict"], review.QUEUE_LAST_FAILURE)
        self.assertEqual((result["queue"])["recent_failure"]["error_class"], "GATE_OFF")
        self.assertEqual(result["verdict"], review.VERDICT_ALIVE_QUIET)

    def test_no_timeout_rule_means_an_old_tick_is_still_quiet(self):
        root, proc = self._live_root()
        _write_summary(root, started_at=_iso(BASE), this_process_started_at=_iso(BASE),
                       last_tick_at=_iso(BASE - timedelta(days=365)))
        result = review.health(root, hostname=HOST, proc_root=proc,
                               now=BASE + timedelta(days=1))
        self.assertEqual(result["verdict"], review.VERDICT_ALIVE_QUIET)
        self.assertTrue(result["progress"]["no_timeout_rule"])
        self.assertGreater(result["progress"]["since_last_tick_seconds"], 86400)


if __name__ == "__main__":
    unittest.main()
