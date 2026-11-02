#!/usr/bin/env python3
"""G1 同一作品恢复：演示脚本的回归入口（只跑这一个可重复用法）。

真正的行为链在 ``scripts/g1_same_work_recovery_demo.py`` 里；这里只确认它在隔离根里
从头到尾能跑通并输出 ``RESULT: PASS``。不重复断言内部细节，也不碰旧实例。
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests"), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_one_day_life import posix_only  # noqa: E402


@posix_only
class G1SameWorkRecoveryTest(unittest.TestCase):
    def test_the_demo_runs_the_whole_same_work_chain(self):
        done = subprocess.run(
            [sys.executable, "-B",
             str(ROOT / "scripts" / "g1_same_work_recovery_demo.py")],
            capture_output=True, text=True, timeout=900,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(done.returncode, 0,
                         (done.returncode, done.stdout[-3000:], done.stderr[-3000:]))
        self.assertIn("RESULT: PASS", done.stdout)
        for line in ("the dropped-receipt write stayed UNKNOWN in AR-0",
                     "only the original operation was reconciled to SUCCEEDED, no bare re-send",
                     "the CAS round ran the formal chain: Decision committed, step bound to the "
                     "same Activity",
                     "the one-shot hook moved the real head after the fence, before the "
                     "adapter's owner call",
                     "the stale formal submission was refused by the owner's CAS",
                     "the NO_ACTION round was legal and wrote nothing"):
            self.assertIn(line, done.stdout, done.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
