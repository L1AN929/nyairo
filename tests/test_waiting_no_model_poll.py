#!/usr/bin/env python3
"""等待维护的零模型契约：upkeep 判定与正式 runner 入口。

这里只钉两件事，都是本次 demo 赖以成立的最小非平凡逻辑：

1. ``waiting_no_model_poll_demo.quiet_upkeep`` 只接受“真的读了 owner、head 没变、什么
   都没花”的维护报告；一次“根本没检查”的空跑（``checked == 0``）必须被判为失败——
   否则零产物就会被当成零调用通过。
2. 正式入口 ``AutonomousLifeRunner.maintain`` 确实把 ``observed_at`` 透传给
   ``cycle.maintain_waiting``，并把 ``llm_calls`` 如实带到报告里（不是 demo 自己编的 0）。

真 owner、AG-1/AG-2、LR-4 的整条等待链由 ``scripts/waiting_no_model_poll_demo.py`` 跑；
这里不重复它，也不模拟业务。

Run (POSIX host -- the life modules import ``fcntl``)::

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_waiting_no_model_poll
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
                str(ROOT / "components" / "life" / "scripts"),
                str(ROOT / "components" / "supply"),
                str(ROOT / "components" / "world" / "scripts")]

import waiting_no_model_poll_demo as demo  # noqa: E402

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "life modules import fcntl (POSIX only)")

QUIET = {
    "state": "OK", "checked": 1, "unchanged": 1, "changed": 0, "evaluated": 0,
    "satisfied": [], "emitted_eligibilities": [], "errors": [],
    "body_checked": 0, "body_changed": 0, "auto_resumed": False, "llm_calls": 0,
}


@POSIX_ONLY
class QuietUpkeepVerdictTest(unittest.TestCase):
    """A zero-model upkeep is only "quiet" when it really re-read the owner."""

    def test_a_real_same_head_upkeep_is_quiet(self) -> None:
        self.assertTrue(demo.quiet_upkeep(dict(QUIET)))

    def test_an_upkeep_that_checked_nothing_is_not_quiet(self) -> None:
        # The exact failure mode this guard exists for: no owner read at all is not "spent nothing".
        self.assertFalse(demo.quiet_upkeep(dict(QUIET, checked=0, unchanged=0)))

    def test_any_model_call_or_change_is_not_quiet(self) -> None:
        for patch in ({"llm_calls": 1}, {"changed": 1, "unchanged": 0},
                      {"evaluated": 1}, {"emitted_eligibilities": ["relig:x"]},
                      {"satisfied": ["rcond:x"]}, {"errors": ["OWNER_HEAD_UNREADABLE:artifact:x"]},
                      {"auto_resumed": True}, {"state": "NOT_WIRED"}):
            self.assertFalse(demo.quiet_upkeep(dict(QUIET, **patch)), patch)


@POSIX_ONLY
class RunnerMaintainEntryTest(unittest.TestCase):
    """``runner.maintain`` is the formal seam the demo uses -- pin its passthrough."""

    def _runner(self, cycle):
        return demo.alr.AutonomousLifeRunner(
            cycle, state_root=Path(tempfile.mkdtemp(prefix="nyairo-wnmp-unit-")),
            env={}, enabled=True)

    def test_maintain_passes_the_instant_and_keeps_llm_calls(self) -> None:
        cycle = _FakeCycle(dict(QUIET))
        runner = self._runner(cycle)
        moment = datetime.fromisoformat("2026-11-20T10:00:00+00:00")
        report = runner.maintain(now=moment)
        self.assertEqual(cycle.calls, [moment.isoformat()])
        self.assertEqual(report.get("llm_calls"), 0)
        self.assertEqual(report.get("checked"), 1)
        # only the report keys travel -- no owner object, no text
        self.assertTrue(set(report).issubset({"state", "checked", "unchanged", "changed",
                                              "evaluated", "satisfied", "emitted_eligibilities",
                                              "errors", "body_checked", "body_changed",
                                              "auto_resumed", "llm_calls"}))

    def test_maintain_returns_none_without_the_cycle_seam(self) -> None:
        self.assertIsNone(self._runner(_FakeCycle(dict(QUIET), wired=False)).maintain(
            now=datetime.fromisoformat("2026-11-20T10:00:00+00:00")))


class _FakeCycle:
    """Only the two attributes ``AutonomousLifeRunner`` touches for ``maintain``."""

    def __init__(self, report, *, wired: bool = True) -> None:
        self.state_root = Path(tempfile.mkdtemp(prefix="nyairo-wnmp-cycle-"))
        self.enabled = True
        self.calls: list = []
        self._report = report
        if not wired:
            self.maintain_waiting = None  # type: ignore[assignment]

    def maintain_waiting(self, *, observed_at=None):
        self.calls.append(str(observed_at))
        return dict(self._report)


if __name__ == "__main__":
    unittest.main()
