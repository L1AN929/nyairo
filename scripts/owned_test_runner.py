#!/usr/bin/env python3
"""按文件独立子进程跑仓库自有测试，避免同进程 sys.modules / sys.path 互相污染。

背景：``tests/`` 没有 ``__init__.py``，而 ``vendor/hermes/tests/__init__.py`` 是常规包。
只要 ``vendor/hermes`` 在 PYTHONPATH 里，``from tests.xxx import ...`` 就会解析到 Hermes
自己的 ``tests`` 包，导致 ModuleNotFoundError；同时单进程 discover 会把 ``phone``、
``scripts`` 等名字缓存成别的东西，后跑的测试拿到错误模块。

这里保持原有的覆盖口径：仍按 ``test_*.py`` 发现 tests 目录下全部文件，仍用 unittest
原用例，只是每个文件一个子进程。任一文件失败，整体非零退出；每个文件的失败/错误
原样打印，不吞、不改断言。

用法：
    python -B scripts/owned_test_runner.py                 # 全部 tests/test_*.py，串行
    python -B scripts/owned_test_runner.py -j 4            # 4 个文件并行
    python -B scripts/owned_test_runner.py --files a.py b.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"

# 与 scripts/test.py 保持一致：只留系统必需变量，测试自己建临时 HOME。
KEEP_ENV = ("PATH", "LANG", "TZ", "SYSTEMROOT")

# 每个文件中立一个空 HOME，避免并行子进程共用 ~/.config 之类的落盘位置。
_HOME_ROOT = Path(tempfile.mkdtemp(prefix="chiyo-owned-tests-"))


def _child_env(path: Path, extra_pythonpath: tuple[Path, ...] = ()) -> dict:
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV}
    # 关键：只把仓库根放进 PYTHONPATH。vendor/hermes 由需要它的测试自己在
    # sys.path 里按正确顺序插入，放这里会让 `tests` 名字先被 Hermes 的包抢走。
    # pytest 路由是例外：pytest 文件按 scripts/test.py 的 cognition-faults 组
    # 口径需要 vendor/hermes，用 extra_pythonpath 显式追加在仓库根之后。
    parts = [str(ROOT)] + [str(extra) for extra in extra_pythonpath]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    home = _HOME_ROOT / path.stem
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["HERMES_HOME"] = str(home)
    return env


def discover() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py") if p.is_file())


#: pytest-style modules, routed explicitly by name.  Nothing else is routed by inspecting the
#: unittest output: a ``Ran 0 tests`` line can also come from an import error, and treating it
#: as "empty" would overwrite the real error with a pytest run.
PYTEST_FILES = frozenset({"test_cognition_reliability.py"})


def _run_unittest(path: Path) -> tuple[str, int, str]:
    cmd = [sys.executable, "-B", "-m", "unittest", "discover",
           "-s", "tests", "-p", path.name, "-q"]
    proc = subprocess.run(cmd, cwd=str(ROOT), env=_child_env(path),
                          capture_output=True, text=True)
    return "PASS" if proc.returncode == 0 else "FAIL", proc.returncode, \
        (proc.stdout or "") + (proc.stderr or "")


def _run_pytest(path: Path) -> tuple[str, int, str]:
    """Route pytest-style files through pytest (same scope as test.py's group)."""

    cmd = [sys.executable, "-B", "-m", "pytest", str(path), "-q"]
    env = _child_env(path, extra_pythonpath=(ROOT / "vendor" / "hermes",))
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env,
                          capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return status, proc.returncode, f"[pytest] {output}"


def run_one(path: Path) -> tuple[str, int, str]:
    started = time.monotonic()
    if path.name in PYTEST_FILES:
        status, code, output = _run_pytest(path)
        runner = "pytest"
    else:
        status, code, output = _run_unittest(path)
        runner = "unittest"
    elapsed = time.monotonic() - started
    return status, code, f"{elapsed:7.1f}s {status} {path.name} ({runner})\n{output}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-j", "--jobs", type=int, default=1)
    parser.add_argument("--files", nargs="*", default=None,
                        help="只跑这些文件名（相对 tests/）")
    args = parser.parse_args()

    files = [TESTS_DIR / name for name in args.files] if args.files else discover()
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        print("MISSING_FILES", missing, flush=True)
        return 2

    started = time.monotonic()
    failed: list[str] = []
    results: list[str] = []

    def collect(result: tuple[str, int, str]) -> None:
        status, code, text = result
        results.append(text)
        if status != "PASS":
            failed.append(text.split("\n")[0].strip())

    jobs = max(1, args.jobs)
    if jobs == 1:
        for path in files:
            collect(run_one(path))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for result in pool.map(run_one, files):
                collect(result)

    header = (f"OWNED_TEST_RUNNER files={len(files)} jobs={jobs} "
              f"elapsed={time.monotonic() - started:.1f}s home_root={_HOME_ROOT}")
    print(header, flush=True)
    for text in results:
        print(text if text.endswith("\n") else text + "\n", flush=True)

    if failed:
        print(f"FAILED_FILES {failed}", flush=True)
        print(f"OWNED_TEST_RUNNER_RESULT FAIL {len(failed)}/{len(files)}", flush=True)
        return 1
    print(f"OWNED_TEST_RUNNER_RESULT PASS {len(files)}/{len(files)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
