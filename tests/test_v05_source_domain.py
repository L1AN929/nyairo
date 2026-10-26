#!/usr/bin/env python3
"""v0.5 来源域窄链：作品来源拒读不得跨域伪造方法退休（单整链 case）。

本模块只守这一块，复用 ``scripts/v05_source_domain_smoke.py`` 的整链：真实 Supply
owner 写出作品 A、真实 Practice owner 在有证 HELPED 后推进 TRIAL、真实 Sharing owner
保存绑定 A 的草稿 D。随后只对 A 的来源读注入拒绝（故障，不是撤权）：

* 正式请求里 D 的正文被扣留并给出 owner 自己的原因，sentinel 不出现在请求/读回/拒绝里；
* 显式 send_local 由 owner 拒且零写（D 的 head/state/receipt 不变）；
* 同一请求的 M0 经历仍可读，Practice owner 的 method_state 仍是 TRIAL，
  作品来源权限不被当成 Memory 证据权限，也不自动退休方法；
* 拒读关闭后下一合法 NO_ACTION 从 owner 重读 A/D 正文，零自动 send/drop/rebind。

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
        import v05_source_domain_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        harness = driver = None
else:  # pragma: no cover
    harness = driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


@POSIX_ONLY
@needs_fixture
class SourceDomainTest(unittest.TestCase):
    """一个隔离实例、一条正式链：A 结果与方法 TRIAL → 草稿 D → A 来源拒读 → 拒发 → 恢复。"""

    def test_refused_work_source_does_not_retire_the_method_or_leak_the_draft(self):
        self.assertIsNotNone(harness)
        self.assertIsNotNone(driver)
        exit_code = driver.main()
        self.assertEqual(exit_code, 0, "the source-domain chain reported a failure")
        self.assertTrue(driver.CHECKS)
        self.assertTrue(all(ok for _name, ok, _detail in driver.CHECKS),
                        [name for name, ok, _detail in driver.CHECKS if not ok])


if __name__ == "__main__":
    unittest.main()
