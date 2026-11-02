#!/usr/bin/env python3
"""One fixed check for the camera response budget + repeated technical tags.

What this really exercises (not just constants):

* the **production** ``phone_tool.phone_handler`` and the real ``PhoneHTTPServer`` over a real
  loopback socket, with a *delayed* answer -- standing in for local translation + generation;
* a caller that hangs up mid-capture (controlled RST, one request, never a flood), which must end
  quietly instead of producing a second internal error;
* the accepted tag line: exact duplicates collapse in order, different counts/colours stay, and
  truncated / over-limit / non-English answers are still refused.

The delays and budgets are scaled down for the test: a 0.35 s default against a 3.0 s camera
budget is the *shape* of the fix, not real model/GPU timing.  Nothing here talks to a real
7860/18190 runtime or any product model.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "tests" / "test_camera_response_tags.py"


def load_tests():
    spec = importlib.util.spec_from_file_location("camera_response_tags_tests", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load %s" % MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_tests()
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    print("camera_response_tags_check: production phone_handler + real loopback HTTP,")
    print("delayed answer and one controlled client disconnect (offsets scaled, no real model).")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failures = len(result.failures) + len(result.errors)
    print("camera_response_tags_check: %s (run=%d failures=%d skipped=%d)"
          % ("OK" if result.wasSuccessful() else "FAILED",
             result.testsRun, failures, len(result.skipped)))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
