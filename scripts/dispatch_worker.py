#!/usr/bin/env python3
"""把一件施工任务派给本地网关上的施工模型。

监工（当前会话）只读、只派工、只验收；真正的代码改动由这个脚本拉起的
worker 进程完成。worker 复用本机的 Claude Code 运行时（读写文件、跑命令），
但模型指向 ``ANTHROPIC_BASE_URL`` 上的另一个模型，因此监工与施工不是同一个模型。

用法::

    python scripts/dispatch_worker.py --name phone-history --task-file task.md
    printf '给 app.js 接上版本列表' | python scripts/dispatch_worker.py --name x

任务正文走 stdin 或文件，不放命令行参数：Windows 下中文参数会被截断。

模型默认 ``workbuddy-wg/cn:deepseek-v4.1-flash``，后面跟一条回退链；只有整个
请求在 API 层失败（网关 5xx、账号池不可用、连不上）才换下一个模型重跑，
业务测试红或超时不换。``--fallback ""`` 可关掉回退，``--dry-run`` 看完整链。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_LOG_DIR = ROOT / ".agents" / "worker-runs"

DEFAULT_BASE_URL = "http://127.0.0.1:3425"
DEFAULT_MODEL = "workbuddy-wg/cn:deepseek-v4.1-flash"
# 主模型挂掉时依次换的模型。只有整个请求在 API 层失败（503/502、账号池不可用、
# 连不上网关）才换；业务测试红、超时、被权限拒绝都不换，免得把真实失败掩盖掉。
DEFAULT_FALLBACKS = (
    "workbuddy-wg/global:deepseek-v4.1-flash",
    "autoclaw-cn/zaicoding_glm-5.3",
    "qoder-cn/qfmodel",
    "auto/gpt-6.1-sol",
)
DEFAULT_TOOLS = "Read,Write,Edit,Grep,Glob,Bash"

# 判定「这次失败是模型/网关的锅，换一个还能重试」的特征串。
API_FAILURE_MARKERS = (
    "API Error: 5",
    "API Error: 429",
    "all accounts are temporarily unavailable",
    "is temporarily unavailable (server error)",
    "Unable to connect",
    "Connection error",
    "ECONNREFUSED",
    "fetch failed",
    "socket hang up",
)

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


def build_command(args: argparse.Namespace, model: str) -> list[str]:
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
    if model:
        cmd += ["--model", model]
    return cmd


def model_chain(args: argparse.Namespace) -> list[str]:
    """主模型在前，回退模型在后，去重保序。"""
    chain: list[str] = []
    for raw in [args.model or "", args.fallback or ""]:
        for m in raw.split(","):
            m = m.strip()
            if m and m not in chain:
                chain.append(m)
    if not chain:
        raise SystemExit("模型链为空：--model 和 --fallback 至少给一个")
    return chain


def is_api_failure(stdout: str, stderr: str, returncode: int) -> bool:
    """整个请求在 API 层就失败了，换模型还有意义。"""
    if returncode == 0:
        return False
    blob = f"{stdout or ''}\n{stderr or ''}"
    return any(marker in blob for marker in API_FAILURE_MARKERS)


def probe_model(model: str, base_url: str, token: str, timeout: float) -> tuple[bool, str]:
    """直接打一次网关，确认这个模型**现在**真的能用。

    claude 自己遇到 503 会退避重试好几分钟才吐错，光靠运行期兜底太慢；
    派工前先探一遍，能把排队的、账号池挂掉的模型直接跳过。
    """
    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/messages",
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": token,
            "authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True, "ok"
    except urllib.error.HTTPError as e:
        detail = e.read()[:160].decode("utf-8", "replace")
        return False, f"HTTP {e.code} {detail}"
    except Exception as e:  # 超时、连不上
        return False, f"{type(e).__name__}: {e}"[:160]


def kill_tree(proc: subprocess.Popen) -> None:
    """连子进程一起杀；Windows 上 claude 是 .CMD 包着一层 node，只杀父进程会留孤儿。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
            return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def run_attempt(
    cmd: list[str],
    task: str,
    workdir: Path,
    env: dict[str, str],
    timeout: int,
) -> tuple[int, str, str, bool, bool]:
    """跑一次模型尝试，边读输出边盯 API 失败特征。

    返回 ``(returncode, stdout, stderr, api_failed, timed_out)``。看到 API 层失败
    （网关 5xx、账号池不可用等）就立刻结束进程，让调用方换下一个模型，不必等
    claude 自己把重试次数耗完——实测 503 时它会退避重试好几分钟。
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(workdir),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    out_buf: list[str] = []
    err_buf: list[str] = []
    api_hit = threading.Event()

    def pump(stream, sink: list[str]) -> None:
        try:
            for line in stream:
                sink.append(line)
                if any(marker in line for marker in API_FAILURE_MARKERS):
                    api_hit.set()
        except Exception:
            pass

    def feed() -> None:
        try:
            proc.stdin.write(task)
            proc.stdin.close()
        except Exception:
            pass

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, out_buf), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err_buf), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    for t in readers:
        t.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    while proc.poll() is None:
        if api_hit.is_set():
            kill_tree(proc)
            break
        if time.monotonic() > deadline:
            timed_out = True
            kill_tree(proc)
            break
        time.sleep(0.2)

    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    for t in readers[:2]:
        t.join(timeout=5)

    return (
        proc.returncode if proc.returncode is not None else -1,
        "".join(out_buf),
        "".join(err_buf),
        api_hit.is_set(),
        timed_out,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="把施工任务派给本地网关模型")
    p.add_argument("--name", default="worker", help="工单名，用于日志文件名")
    p.add_argument("--task-file", help="任务正文文件；省略则从 stdin 读")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"施工模型，默认 {DEFAULT_MODEL}")
    p.add_argument(
        "--fallback",
        default=",".join(DEFAULT_FALLBACKS),
        help="API 层失败时依次换的模型，逗号分隔；传空字符串关闭回退",
    )
    p.add_argument("--base-url", default=os.environ.get("WORKER_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--dir", default=str(ROOT), help="worker 的工作目录")
    p.add_argument(
        "--max-turns",
        type=int,
        default=300,
        help="工具往返上限；只读核查给 20-30，施工给 200-300，默认 300",
    )
    p.add_argument("--timeout", type=int, default=3600, help="秒；超时即杀")
    p.add_argument("--allowed-tools", default=DEFAULT_TOOLS)
    p.add_argument("--token", default=os.environ.get("WORKER_AUTH_TOKEN", "local-gateway"))
    p.add_argument("--no-probe", action="store_true", help="派工前不探测模型可用性，直接按链跑")
    p.add_argument("--probe-timeout", type=float, default=8.0, help="单个模型探测超时秒数")
    p.add_argument("--dry-run", action="store_true", help="只打印命令和环境，不执行")
    args = p.parse_args()

    task = read_task(args)
    chain = model_chain(args)
    workdir = Path(args.dir)

    base_env = dict(os.environ)
    base_env.update(
        {
            "ANTHROPIC_BASE_URL": args.base_url,
            "ANTHROPIC_AUTH_TOKEN": args.token,
        }
    )

    def env_for(model: str) -> dict[str, str]:
        env = dict(base_env)
        env.update(
            {
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            }
        )
        return env

    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = RUN_LOG_DIR / f"{stamp}-{args.name}.log"

    if args.dry_run:
        print(f"cwd:   {workdir}")
        print(f"chain: {' -> '.join(chain)}")
        print(f"base:  {args.base_url}")
        print(f"log:   {log_path}")
        for m in chain:
            print(f"cmd[{m}]:", " ".join(build_command(args, m)))
        print("--- task ---")
        print(task)
        return 0

    probe_notes: list[str] = []
    if not args.no_probe:
        chosen = None
        for model in chain:
            ok, detail = probe_model(model, args.base_url, args.token, args.probe_timeout)
            probe_notes.append(f"{model}: {'ok' if ok else detail}")
            if ok:
                chosen = model
                break
        if chosen is None:
            print("[worker] 回退链上所有模型现在都不可用：", file=sys.stderr)
            for note in probe_notes:
                print(f"  {note}", file=sys.stderr)
            return 3
        if chosen != chain[0]:
            print(f"[worker] 主模型探测失败，改用 {chosen}", file=sys.stderr)
        chain = [chosen] + [m for m in chain if m != chosen]

    header = (
        f"# worker run {stamp}\n"
        f"# model: {chain[0]}\n"
        f"# fallback: {', '.join(chain[1:]) or '-'}\n"
        + "".join(f"# probe: {note}\n" for note in probe_notes)
        + f"# cwd:   {workdir}\n"
        f"# task:\n{task}\n\n"
    )
    used = chain[0]
    rc = 1
    last_out = ""
    with log_path.open("w", encoding="utf-8") as log:
        log.write(header)
        log.flush()
        for idx, model in enumerate(chain):
            used = model
            if idx:
                log.write(f"\n# --- retry #{idx}: {model}（上一次在 API 层失败）---\n")
                print(f"[worker] 换模型重试：{model}", file=sys.stderr)
            rc, out, err, api_failed, timed_out = run_attempt(
                build_command(args, model), task, workdir, env_for(model), args.timeout
            )
            last_out = out
            log.write(out)
            if err:
                log.write("\n# stderr:\n")
                log.write(err)
            log.write(f"\n# exit: {rc}\n")
            log.flush()
            if timed_out:
                log.write(f"# TIMEOUT after {args.timeout}s (model: {model})\n")
                print(f"施工超时（{args.timeout}s，模型 {model}），日志：{log_path}")
                return 124
            if not (api_failed or is_api_failure(out, err, rc)):
                break
        else:
            log.write("\n# 所有模型都在 API 层失败，未进入施工\n")
            print(f"[worker] 回退链全部失败：{' -> '.join(chain)}", file=sys.stderr)

    print(last_out.rstrip())
    print(f"\n[worker] exit={rc} model={used} log={log_path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
