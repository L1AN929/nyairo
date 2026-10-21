#!/usr/bin/env python3
"""把一件施工任务派给本地网关上的施工模型。

监工（当前会话）只读、只派工、只验收；真正的代码改动由这个脚本拉起的
worker 进程完成。worker 复用本机的 Claude Code 运行时（读写文件、跑命令），
但模型指向 ``ANTHROPIC_BASE_URL`` 上的另一个模型，因此监工与施工不是同一个模型。

用法::

    python scripts/dispatch_worker.py --name phone-history --task-file task.md
    printf '给 app.js 接上版本列表' | python scripts/dispatch_worker.py --name x

任务正文走 stdin 或文件，不放命令行参数：Windows 下中文参数会被截断。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_LOG_DIR = ROOT / ".agents" / "worker-runs"

DEFAULT_BASE_URL = "http://127.0.0.1:3425"
DEFAULT_MODEL = "workbuddy-wg/cn:deepseek-v4.1-flash"
DEFAULT_TOOLS = "Read,Write,Edit,Grep,Glob,Bash"

WORKER_RULES = """\
你是一线施工代理，不是方案设计者。监工已经定好范围，你只负责在范围内把活干完。

纪律：
1. 先读相关文件再改，禁止凭猜测改接口。
2. 只改任务指定的写集；写集外的文件一律不动，不改无关风格，不新增依赖。
3. 不做与任务无关的重构，不做过度校验（不为了证明完整性去算 SHA256 或逐字节比对）。
4. 服务端契约不够用时停下来说明，不许擅自改契约。
5. 遇到需要产品决策的分歧，列出选项后停止，交给监工。
6. 改完必须实际运行相关测试或命令，不能只说“应该没问题”。

交付时按这四项汇报：
- 改了哪些文件
- 跑了哪条命令
- 结果是什么（通过/失败，失败贴关键输出）
- 还有什么没验证
"""


def read_task(args: argparse.Namespace) -> str:
    if args.task_file:
        text = Path(args.task_file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        raise SystemExit("任务正文为空：用 --task-file 或 stdin 传入")
    return text


def build_command(args: argparse.Namespace) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("找不到 claude 可执行文件")
    cmd = [
        exe,
        "-p",
        "--output-format",
        "text",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        args.allowed_tools,
        "--max-turns",
        str(args.max_turns),
        "--append-system-prompt",
        WORKER_RULES,
    ]
    if args.model:
        cmd += ["--model", args.model]
    return cmd


def main() -> int:
    p = argparse.ArgumentParser(description="把施工任务派给本地网关模型")
    p.add_argument("--name", default="worker", help="工单名，用于日志文件名")
    p.add_argument("--task-file", help="任务正文文件；省略则从 stdin 读")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"施工模型，默认 {DEFAULT_MODEL}")
    p.add_argument("--base-url", default=os.environ.get("WORKER_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--dir", default=str(ROOT), help="worker 的工作目录")
    p.add_argument("--max-turns", type=int, default=80)
    p.add_argument("--timeout", type=int, default=3600, help="秒；超时即杀")
    p.add_argument("--allowed-tools", default=DEFAULT_TOOLS)
    p.add_argument("--token", default=os.environ.get("WORKER_AUTH_TOKEN", "local-gateway"))
    p.add_argument("--dry-run", action="store_true", help="只打印命令和环境，不执行")
    args = p.parse_args()

    task = read_task(args)
    cmd = build_command(args)
    workdir = Path(args.dir)

    env = dict(os.environ)
    env.update(
        {
            "ANTHROPIC_BASE_URL": args.base_url,
            "ANTHROPIC_AUTH_TOKEN": args.token,
            "ANTHROPIC_MODEL": args.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": args.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": args.model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": args.model,
        }
    )

    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = RUN_LOG_DIR / f"{stamp}-{args.name}.log"

    if args.dry_run:
        print(f"cwd: {workdir}")
        print(f"model: {args.model}")
        print(f"base:  {args.base_url}")
        print(f"log:   {log_path}")
        print("cmd:  ", " ".join(cmd))
        print("--- task ---")
        print(task)
        return 0

    header = (
        f"# worker run {stamp}\n"
        f"# model: {args.model}\n"
        f"# cwd:   {workdir}\n"
        f"# task:\n{task}\n\n"
    )
    with log_path.open("w", encoding="utf-8") as log:
        log.write(header)
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                input=task,
                cwd=str(workdir),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n# TIMEOUT after {args.timeout}s\n")
            print(f"施工超时（{args.timeout}s），日志：{log_path}")
            return 124
        log.write(proc.stdout or "")
        if proc.stderr:
            log.write("\n# stderr:\n")
            log.write(proc.stderr)
        log.write(f"\n# exit: {proc.returncode}\n")

    print(proc.stdout.rstrip())
    print(f"\n[worker] exit={proc.returncode} log={log_path}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
