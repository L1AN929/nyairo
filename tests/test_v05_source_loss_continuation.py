#!/usr/bin/env python3
"""v0.5 来源损失窄链：归档后不被偷换、旧草稿拒发（单整链 case）。

本模块只守这一块，复用 ``scripts/v05_source_loss_continuation_smoke.py`` 的整链：
真实 Supply owner 归档作品 A 后，当前 Activity 仍点名 A —— 若 CONTINUE 候选还在，
选它得到具名 ``WORK_NOT_CONTINUABLE``；若候选已被撤掉，A 的非法回答被 AG-1 拒。
两种情况都不写 A/B 版本、不产生执行 receipt/新 handoff，也不 fallback 到可续的 B。
草稿 D 的来源 ARCHIVED 仍可读，send_local 由 owner 拒 ``SOURCE_ARCHIVED`` 且零写；
后续无 sharing 的 NO_ACTION 零写，不自动 drop/rebind/send。

真实件：真实 Supply owner、真实 M0 Memory、真实 ProductionLifeRuntime、真实
Sharing / Practice owner、AG-0/AG-1/AG-2/AR-0。替身只有 Decision 与内容回答。
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
        import result_method_behavior_smoke as harness  # noqa: E402
        import v05_source_loss_continuation_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        harness = driver = None
else:  # pragma: no cover
    harness = driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


@POSIX_ONLY
@needs_fixture
class SourceLossContinuationTest(unittest.TestCase):
    """一个隔离实例、一条正式链：真实作品 → 草稿 → 合法归档 → 具名拒绝 → 拒发 → 安静。"""

    def test_archived_source_is_refused_by_name_and_the_draft_cannot_be_sent(self):
        self.assertIsNotNone(harness)
        self.assertIsNotNone(driver)
        exit_code = driver.main()
        self.assertEqual(exit_code, 0, "the source-loss chain reported a failure")
        self.assertTrue(driver.CHECKS)
        self.assertTrue(all(ok for _name, ok, _detail in driver.CHECKS),
                        [name for name, ok, _detail in driver.CHECKS if not ok])


if __name__ == "__main__":
    unittest.main()
