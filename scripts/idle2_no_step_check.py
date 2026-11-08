#!/usr/bin/env python3
"""idle2 移动 Activity 重复 CONTINUE 无步：唯一离线入口（只读，不碰正式根）。"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

PROFILE = {"run_id": "run:fastday-idle2-20261106", "product_calls": 0,
           "formal_root_touched": False}


def offline_check():
    """唯一验收：本缺口定向用例 + 一条正式短链，网络硬拒、AF_UNIX 放行。"""
    import g1_real_adoption as g1
    import g2_explore_c as shared

    scratch = Path(tempfile.mkdtemp(prefix="idle2-nostep-offline-"))
    guard = shared._install_child_guard(scratch / "guard")
    try:
        with g1._offline_network_guard(scratch):
            selfcheck = shared._guard_selfcheck()
            suite = unittest.defaultTestLoader.discover(
                str(REPO / "tests"), pattern="test_idle2_no_step.py")
            output = io.StringIO()
            result = unittest.TextTestRunner(stream=output, verbosity=0).run(suite)
            child = shared._child_guard_status(guard["status_dir"])
            # This check's owners run in-thread (AF_UNIX); no child process is expected, so
            # "no child at all" is acceptable -- a spawned child must still refuse.
            child_ok = bool(child["all_refuse"] or child["processes"] == 0)
            ok = (result.testsRun > 0 and not result.skipped and result.wasSuccessful()
                  and all(selfcheck.values()) and child_ok)
            return {"ok": ok, "tests": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "skipped": len(result.skipped),
                    "failed_cases": [test.id() for test, _ in result.failures + result.errors],
                    "error_locations": [[(Path(p).name, int(n)) for p, n in
                                         re.findall(r'File "([^"]+)", line ([0-9]+)', trace)]
                                        for _, trace in result.failures + result.errors],
                    "refusal_codes": [re.findall(r"'reason(?:_code)?': '([A-Z_]+)'", trace)
                                      for _, trace in result.failures + result.errors],
                    "network_guard": selfcheck, "child_guard": child,
                    "scratch": str(scratch), "formal_root_touched": False,
                    "product_calls": 0, "profile": PROFILE}
    finally:
        shared._restore_child_guard(guard)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("offline-check",))
    args = parser.parse_args(argv)
    try:
        result = {"offline-check": offline_check}[args.command]()
    except Exception as exc:
        result = {"ok": False, "reason_code": "IDLE2_NO_STEP_ENTRY_FAILED",
                  "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
