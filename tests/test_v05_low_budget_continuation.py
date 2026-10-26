#!/usr/bin/env python3
"""v0.5 EP17 低预算诚实接续：既有预算门与停点的正式整链（单 case）。

本模块只守这一块，复用 ``scripts/v05_low_budget_continuation_smoke.py`` 的四个隔离根：
正式 runner check-window → 真实请求前预留 → 真实 Supply/AG-0/AG-1/AG-2/AR-0 →
run 窗口与 UTC 日两个层各自如实拒绝；剩余 0 时不发请求、不虚增版本/Activity/receipt/
Memory；剩余 1 时 Decision 已付费提交而内容请求在预留处被拒（该轮按模型决策轮记
`llm_calls=1`）；NO_ACTION 不需要内容；同根同进程重装保持用量与停点，坏账 fail-closed；
账本日读口注入的日界只滚当日额度，并验证断步之后原 run 仍有余额时新 Decision 依法
CONTINUE 同一 Activity/作品、旧成功不重放。

真实件：真实 Supply owner、真实 M0 Memory、真实 ProductionLifeRuntime、正式 runner、
AG-0/AG-1/AG-2/AR-0。替身只有 Decision 与内容回答（脚本，明示）。
POSIX 专用（``fcntl`` + AF_UNIX）；原生 Windows 上整模块 skip。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

POSIX_ONLY = unittest.skipUnless(os.name == "posix",
                                 "real Supply socket needs POSIX (WSL/Linux)")

_IMPORT_ERROR: str | None = None
if os.name == "posix":  # pragma: no branch
    try:
        import v05_low_budget_continuation_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        driver = None
else:  # pragma: no cover
    driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


@POSIX_ONLY
@needs_fixture
class LowBudgetContinuationTest(unittest.TestCase):
    """一个隔离链：低额度写一版 → 剩余 0 拒绝 → 剩余 1 发后拒绝 → 日界只滚当日。"""

    def test_the_budget_gate_refuses_honestly_and_the_stop_point_survives(self):
        self.assertIsNotNone(driver)
        exit_code = driver.main()
        self.assertEqual(exit_code, 0, "the low-budget chain reported a failure")
        self.assertTrue(driver.CHECKS)
        self.assertTrue(all(ok for _name, ok, _detail in driver.CHECKS),
                        [name for name, ok, _detail in driver.CHECKS if not ok])
        # 两个原缺口不能靠"0 或 1 皆可"混过：把具名反例和精确计数在这里再钉一次。
        names = {name for name, _ok, _detail in driver.CHECKS}
        for required in (
            "a round that paid a Decision and had its content refused counts one model round",
            "a later Decision legally continues the same Activity and the same work",
            "the previously adopted Decision is not re-adopted or replayed",
            "the run keeps its total (3 + 2 of 6); only the new day's two calls count",
        ):
            self.assertIn(required, names)
        rounds = {(row["fixture"], row["round"]): row for row in driver.ROUNDS}
        refused = rounds[("c", "last-request")]
        self.assertEqual(refused["outcome"], "BUDGET_EXHAUSTED")
        self.assertEqual(refused["llm_calls"], 1)
        followed = rounds[("c", "after-refusal-day-roll")]
        self.assertEqual(followed["outcome"], "ACTED")
        self.assertEqual(followed["llm_calls"], 1)
        self.assertIs(followed["owner_version_created"], True)


if __name__ == "__main__":
    unittest.main()
