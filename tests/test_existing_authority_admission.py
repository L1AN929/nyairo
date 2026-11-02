"""直接测试：既有 grant 撤权/竞态与 writer fence 的真实链（不调用模型）。

只跑 ``scripts/existing_authority_admission_demo.py`` 这一条真实 owner 路径，不重复实现
一份准入；隔离 root 由 demo 自己创建并保留。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _load_demo():
    path = ROOT / "scripts" / "existing_authority_admission_demo.py"
    spec = importlib.util.spec_from_file_location("existing_authority_admission_demo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(hasattr(os, "geteuid"), "POSIX-only: AF_UNIX + SO_PEERCRED")
class ExistingAuthorityAdmissionTest(unittest.TestCase):
    def test_real_owner_grant_and_fence_paths(self) -> None:
        self.assertEqual(_load_demo().main(), 0)


if __name__ == "__main__":
    unittest.main()
