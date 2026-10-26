#!/usr/bin/env python3
"""v0.5 同一结果作品——方法证据——草稿的来源损失（单整链 case）。

本模块只守这一块，复用 ``scripts/v05_same_result_loss_smoke.py`` 的整链：真实 Supply
owner 写出作品 A 的结果 R、真实 M0 经历与 owner 结果身份一致、真实 Practice owner 在
有证 HELPED 后推进 TRIAL、真实 Sharing owner 保存绑定 A/R 的草稿 D。随后只对 A 的
来源读注入拒绝（故障，不是撤权）：

* 正式请求里 D 的正文被扣留并给出 owner 自己的原因，sentinel 不出现在请求/读回/拒绝里；
* 显式 send_local 由 owner 拒且零写（D 的 head/state/receipt 不变）；
* 同一请求的 M0 经历仍可读、Practice owner 的 method_state 仍是 TRIAL，
  作品来源权限不被当成 Memory 证据权限，也不自动退休方法；
* 故障解除后下一合法 NO_ACTION 从 owner 重读 A/D 正文，零自动 send/drop/rebind；
* 同一真实 owner 合法 archive A 后版本/head/正文保留，当前目标 A 不可续且不 fallback
  到另建的可续 B，D 的来源 ARCHIVED 仍可读但发送门拒且零写。

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
        import v05_same_result_loss_smoke as driver  # noqa: E402
    except Exception as error:  # noqa: BLE001
        _IMPORT_ERROR = "%s: %s" % (type(error).__name__, error)
        harness = driver = None
else:  # pragma: no cover
    harness = driver = None

needs_fixture = unittest.skipIf(_IMPORT_ERROR is not None,
                                "fixture unavailable: %s" % (_IMPORT_ERROR or ""))


@POSIX_ONLY
@needs_fixture
class SameResultLossTest(unittest.TestCase):
    """一个隔离实例、一条正式链：A/R 结果 → 方法 TRIAL → 草稿 D → A 来源拒读 → 归档。"""

    def test_the_same_result_anchors_method_evidence_and_draft_through_source_loss(self):
        self.assertIsNotNone(harness)
        self.assertIsNotNone(driver)
        exit_code = driver.main()
        self.assertEqual(exit_code, 0, "the same-result source-loss chain reported a failure")
        self.assertTrue(driver.CHECKS)
        self.assertTrue(all(ok for _name, ok, _detail in driver.CHECKS),
                        [name for name, ok, _detail in driver.CHECKS if not ok])


if __name__ == "__main__":
    unittest.main()
