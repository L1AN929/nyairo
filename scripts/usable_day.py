#!/usr/bin/env python3
"""本机「可用」入口：把手机页面和她自己的运行窗口分开。

    python scripts/usable_day.py serve  --root /abs/phone-root --port 3831
    python scripts/usable_day.py view   --root /abs/phone-root [--text]
    python scripts/usable_day.py window --root /abs/phone-root \\
        [--duration-seconds 120] [--max-new-requests 1]
    python scripts/usable_day.py pause  --root /abs/phone-root
    python scripts/usable_day.py resume --root /abs/phone-root
    python scripts/usable_day.py stop   --root /abs/phone-root

``serve`` 只起手机面（既有的 ``phone.demo.serve_instance``：Supply/World 子进程 + loopback
HTTP），**不启动 resident、不发模型请求**。真实运行只能由一个明确的 ``window`` 触发：它复用
既有 ``phone.live_day.run_segment``，并且受本功能共享的 4 次请求上限约束——上限从持久请求
账本推回，监督进程重启、窗口崩溃都不会刷新它。

``view`` / ``status`` 是同一份纯读投影的两种输出；``view`` 给手机页面看的形状，``status``
更接近 ``live_day status`` 的原始结构。两者都不写、不发请求。

``stop`` 只停本监督记录里那个自己启动的窗口子进程，绝不对不是自己起的 resident 发信号。
要停监管之外的 resident，用既有的 ``scripts/live_day.py stop``。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_ROOT), str(REPO_ROOT / "components"),
               str(REPO_ROOT / "components" / "life" / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def _emit(payload, as_text: str | None = None) -> None:
    if as_text:
        sys.stdout.write(as_text.rstrip("\n") + "\n")
    else:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _living():
    from components.phone import living

    return living


def _text_view(view: dict) -> str:
    lines = [str(view.get("sentence") or "读不到当前状态。")]
    where = (view.get("where") or {})
    if where.get("status") == "READY":
        lines.append("位置: %s (%s)" % (where.get("place_name") or where.get("place_id"),
                                        where.get("source")))
    else:
        lines.append("位置: 未知 (%s)" % ",".join(where.get("unknowns") or []))
    work = view.get("work") or {}
    if work.get("title"):
        lines.append("作品停点: 《%s》 %s" % (work.get("title"), work.get("head_version_id")))
    elif work.get("activity_ref"):
        lines.append("上次活动: %s" % work.get("activity_ref"))
    reading = view.get("reading") or {}
    if reading.get("title"):
        lines.append("阅读: 《%s》 第 %s/%s 页" % (reading.get("title"), reading.get("page"),
                                              reading.get("pages")))
    nxt = (view.get("next") or {})
    if nxt.get("check_window_at"):
        lines.append("下一次自然窗口: %s" % nxt.get("check_window_at"))
    mem = view.get("memory") or {}
    if mem.get("binding_status"):
        lines.append("Memory: %s / recall_ready=%s" % (mem.get("binding_status"),
                                                       mem.get("recall_ready")))
    else:
        lines.append("Memory: 未读到（这条命令没有手机服务；页面上会读真实绑定）")
    run = view.get("run") or {}
    capacity = run.get("capacity") or {}
    lines.append("运行: resident=%s pause=%s 本功能已用=%s/%s" % (
        (run.get("resident") or {}).get("running_recorded"),
        (run.get("pause") or {}).get("paused"),
        capacity.get("used"), capacity.get("function_cap")))
    lines.append("观测时间: %s" % view.get("observed_at"))
    return "\n".join(lines)


def cmd_view(argv: list[str]) -> int:
    living = _living()
    parser = argparse.ArgumentParser(prog="usable_day view")
    parser.add_argument("--root", required=True)
    parser.add_argument("--live", action="store_true",
                        help="also ask the already-running World owner for one bounded look")
    parser.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)
    view = living.living_view(Path(args.root), live=bool(args.live))
    _emit(view, _text_view(view) if args.text else None)
    return EXIT_OK


def cmd_status(argv: list[str]) -> int:
    from components.phone import live_day

    parser = argparse.ArgumentParser(prog="usable_day status")
    parser.add_argument("--root", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    report = live_day.status(Path(args.root), live=bool(args.live))
    _emit(report)
    return int(report.get("exit_code", EXIT_REFUSED))


def cmd_serve(argv: list[str]) -> int:
    from components.phone import demo, living

    parser = argparse.ArgumentParser(prog="usable_day serve")
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=3831)
    parser.add_argument("--ready-timeout", type=float, default=demo.CHILD_READY_TIMEOUT)
    parser.add_argument("--tick-seconds", type=float, default=demo.DEFAULT_WORLD_TICK_SECONDS)
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        supervisor = living.Supervisor(root, port=int(args.port))
        announcement = demo.serve_instance(
            root, port=int(args.port), ready_timeout=float(args.ready_timeout),
            tick_seconds=float(args.tick_seconds), supervisor=supervisor)
    except Exception as exc:  # never echo paths/keys, but never flatten the real reason either
        payload = {"ok": False, "status": "error", "reason_code": "USABLE_SERVE_FAILED",
                   "error_type": type(exc).__name__}
        codes = demo.safe_reason_codes(exc)
        if codes:
            payload["serve_reason_code"] = codes[0]
            payload["serve_reasons"] = codes
        _emit(payload)
        return EXIT_REFUSED
    _emit(announcement)
    return EXIT_OK


def cmd_window(argv: list[str]) -> int:
    living = _living()
    parser = argparse.ArgumentParser(prog="usable_day window")
    parser.add_argument("--root", required=True)
    parser.add_argument("--duration-seconds", type=int, default=living.DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--max-new-requests", type=int, default=1)
    args = parser.parse_args(argv)
    report = living.run_bounded_window(
        Path(args.root), duration_seconds=args.duration_seconds,
        max_new_requests=args.max_new_requests)
    _emit(report)
    if report.get("ok"):
        return _exit_code(report.get("exit_code"))
    return EXIT_REFUSED


def cmd_pause(argv: list[str], *, paused: bool) -> int:
    living = _living()
    parser = argparse.ArgumentParser(prog="usable_day %s" % ("pause" if paused else "resume"))
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    result = living.set_paused(Path(args.root), paused)
    _emit(result)
    return EXIT_OK if result.get("ok") else EXIT_REFUSED


def cmd_stop(argv: list[str]) -> int:
    """Stop only the window child this supervisor itself recorded."""

    living = _living()
    parser = argparse.ArgumentParser(prog="usable_day stop")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    record = living._read_json(living.supervisor_path(root))
    child = dict(record.get("child") or {})
    pid = child.get("pid")
    supervisor_pid = record.get("pid")
    if not record or not pid:
        _emit({"ok": False, "status": "IDLE", "reason": "NO_SUPERVISED_WINDOW",
               "detail": "没有本监督启动的窗口；不会去停不是自己起的进程"})
        return EXIT_REFUSED
    if str(record.get("root") or "") not in ("", str(root)):
        _emit({"ok": False, "status": "BLOCKED", "reason": "SUPERVISOR_RECORD_ROOT_MISMATCH"})
        return EXIT_REFUSED
    if not _pid_alive(supervisor_pid):
        _emit({"ok": False, "status": "BLOCKED", "reason": "SUPERVISOR_PROCESS_GONE",
               "detail": "监督进程已不在；它启动的子进程也不再由本入口管理"})
        return EXIT_REFUSED
    if not _pid_alive(pid):
        _emit({"ok": True, "status": "ALREADY_STOPPED", "reason": "CHILD_NOT_RUNNING",
               "pid": pid})
        return EXIT_OK
    if os.name != "posix":
        _emit({"ok": False, "status": "BLOCKED",
               "reason": "SIGNAL_STOP_UNSUPPORTED_ON_PLATFORM"})
        return EXIT_REFUSED
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError as exc:
        _emit({"ok": False, "status": "BLOCKED", "reason": "SIGNAL_FAILED",
               "detail": type(exc).__name__})
        return EXIT_REFUSED
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    _emit({"ok": True, "status": "STOPPED" if not _pid_alive(pid) else "STOP_REQUESTED",
           "pid": pid, "supervised_only": True})
    return EXIT_OK


def _pid_alive(pid) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _exit_code(value) -> int:
    if value is None or isinstance(value, bool):
        return EXIT_REFUSED
    try:
        return int(value)
    except (TypeError, ValueError):
        return EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write("usage: usable_day.py {serve,view,status,window,pause,resume,stop} "
                         "--root <phone-root>\n")
        return EXIT_OK if arguments else EXIT_USAGE
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % arguments[0])
        return EXIT_USAGE
    return int(handler(arguments[1:]))


COMMANDS = {
    "serve": cmd_serve,
    "view": cmd_view,
    "status": cmd_status,
    "window": cmd_window,
    "stop": cmd_stop,
    "pause": lambda argv: cmd_pause(argv, paused=True),
    "resume": lambda argv: cmd_pause(argv, paused=False),
}


if __name__ == "__main__":
    sys.exit(main())
