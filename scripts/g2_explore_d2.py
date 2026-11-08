#!/usr/bin/env python3
"""G2 explore-d2：C 故障后继的薄入口（离线可测；真实 prepare/run 由父级执行）。

本文件**不复制**实验仪器，只在 ``g2_explore_c`` 的共享实现上换一份封闭 profile：

* 身份 ``run:g2-explore-20261103-d2`` / 域 ``g2:explore:20261103-d2``；
* root ``/tmp/nyairo-g2-explore-20261103-d2``（``main`` / ``memory-authority`` / ``branches``）；
* 历史 239 / 跨批累计 1000 / 单根 UTC 300；新增 cap 30 = 采集 8 + 影子 20（10 对）+ 保留 2；
* 总窗 7200 真秒（采集最多 5400）、观察 60 秒/次；**检查节拍 10 分钟**（C/旧批仍 30）；
* 绝对 2026-11-03 21:00 CST 停新请求、19:00 CST 最晚启动；首次故障停批、不二 run。

边界与 C 相同：不 source 密钥、不探测产品 endpoint、不读写旧根、不删数据；``run`` 只在
``--execute-approved`` 下执行，正式根永远不能夹具运行；CLI 只输出固定字段/枚举/计数。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts"),
               str(REPO_ROOT / "components" / "native"),
               str(REPO_ROOT / "components" / "native" / "memory_runtime_v1"),
               str(REPO_ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import g2_explore_c as shared  # noqa: E402
import g2_next_live as live  # noqa: E402

EXIT_OK = shared.EXIT_OK
EXIT_ERROR = shared.EXIT_ERROR
EXIT_REFUSED = shared.EXIT_REFUSED

PROFILE = live.D2_PROFILE
BATCH = live.BATCH_D2
TEST_PATTERN = "test_g2_explore_d2.py"
PROG = "g2_explore_d2"


def prepare(argv: Sequence[str]) -> int:
    return shared.prepare(argv, profile=PROFILE, batch=BATCH, prog=PROG)


def audit(argv: Sequence[str]) -> int:
    return shared.audit(argv, profile=PROFILE, batch=BATCH, prog=PROG)


def run(argv: Sequence[str]) -> int:
    return shared.run(argv, profile=PROFILE, batch=BATCH, prog=PROG)


def status(argv: Sequence[str]) -> int:
    return shared.status(argv, profile=PROFILE, prog=PROG)


def _offline_check(_argv: Sequence[str]) -> int:
    # D2 只跑一条短正式链（tick/维护 trace/预算接线）；C 的 on/off 对照与旧套件不重做。
    return shared._emit_offline_check(_argv, profile=PROFILE, test_pattern=TEST_PATTERN,
                                      observer_equivalence=False)


COMMANDS = {"offline-check": _offline_check, "prepare": prepare, "audit": audit,
            "run": run, "status": status}


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write(__doc__ or "")
        return EXIT_OK if arguments else EXIT_REFUSED
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % (arguments[0],))
        return EXIT_REFUSED
    try:
        return int(handler(arguments[1:]))
    except SystemExit:
        raise
    except Exception as exc:
        # 顶层安全壳：只报固定 reason_code 与异常类型名，不输出正文或 traceback。
        sys.stdout.write(shared.json.dumps(
            {"ok": False, "reason_code": "TOP_LEVEL_EXCEPTION",
             "kind": type(exc).__name__}, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stdout.flush()
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
