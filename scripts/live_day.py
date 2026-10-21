#!/usr/bin/env python3
"""v0.4 C: 连续生活运行入口 —— 一条命令让**已有**手机实例过一段连续的日常。

    python scripts/live_day.py run --instance /abs/phone-root \\
        [--duration-seconds 600] [--max-ticks N] [--poll-seconds N] \\
        [--max-new-requests 6] [--services auto|always|never] [--no-act] [--text]

    python scripts/live_day.py status --instance /abs/phone-root [--live] [--text]
    python scripts/live_day.py stop   --instance /abs/phone-root

这不是第二个生活总脑，也不是新的服务：

* ``run`` 复用 ``phone.demo.serve_instance``（这台实例自己的 Supply/World 子进程；已经在跑就一个都不起）
  与 ``phone.life.life_run``（同一个 ``AutonomousLifeResident``、同一把 writer lease、同一份 run 窗口）。
  默认有限；``--max-new-requests`` 只会把这**这一段**的请求上限压低（``min(既有, 已用+ N)``），
  绝不开新窗口、绝不清 quiet、绝不提高任何既有预算。
* ``status`` 纯读：不启动服务、不建库、不绑 socket；读不到的 owner 事实如实报 ``UNKNOWN``。
* ``stop`` 只给这台实例自己记录里的那个 resident 发 SIGTERM（POSIX）；Windows 上会拒绝并说明用 Ctrl-C。

返回值：0 正常结束，3 被现有规则挡住（含预算/lease），1 读不到实例/被拒，2 用法，130 取消。
细节与证据见 ``docs/engineering/v04-lived-day-runtime-result.md``。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def _emit(payload, as_text: str | None = None) -> None:
    if as_text:
        sys.stdout.write(as_text.rstrip("\n") + "\n")
    else:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _load():
    from components.phone import live_day

    return live_day


def _run_text(live_day, report: dict) -> str:
    summary = report.get("summary") or {}
    lines: list[str] = []
    if summary and summary.get("schema"):
        lines.append(live_day.summary_text(summary))
    lines.append("本段: %s ticks=%s 请求(本段)=%s 服务启动=%s lease=%s" % (
        report.get("status"), (summary.get("segment") or {}).get("ticks_recorded"),
        report.get("model_calls"), bool((report.get("services") or {}).get("started")),
        (report.get("lease") or {}).get("status")))
    if report.get("reason"):
        lines.append("原因: %s %s" % (report.get("reason"), report.get("detail") or ""))
    return "\n".join(lines)


def _status_text(live_day, report: dict) -> str:
    summary = report.get("summary") or {}
    lines: list[str] = []
    if summary and summary.get("schema"):
        lines.append(live_day.summary_text(summary))
    lines.append("记录: running=%s pid=%s lease=%s live_owner=%s" % (
        report.get("running_recorded"), report.get("pid"),
        (report.get("lease") or {}).get("status"),
        (report.get("live_owner_read") or {}).get("status")))
    if report.get("note"):
        lines.append("注意: %s" % report["note"])
    if report.get("reason"):
        lines.append("原因: %s %s" % (report.get("reason"), report.get("detail") or ""))
    return "\n".join(lines)


def cmd_run(argv: list[str]) -> int:
    live_day = _load()
    parser = argparse.ArgumentParser(prog="live_day run")
    parser.add_argument("--instance", required=True,
                        help="the existing phone instance root (the one ``phone serve`` uses)")
    parser.add_argument("--duration-seconds", type=int, default=live_day.DEFAULT_DURATION_SECONDS,
                        help="finite wall-clock bound (default %d)" % live_day.DEFAULT_DURATION_SECONDS)
    parser.add_argument("--max-ticks", type=int, default=None,
                        help="tighter bound: stop after this many loop ticks of THIS segment")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="the resident's own poll interval (default: its own)")
    parser.add_argument("--max-new-requests", type=int, default=None,
                        help="optional LOWER request cap for this segment only "
                             "(min(configured, already-used + N)); never opens a window")
    parser.add_argument("--services", default=live_day.SERVICE_AUTO,
                        choices=list(live_day.SERVICE_MODES),
                        help="auto: start the owners only when they do not answer (default); "
                             "always: start this segment's own generation; "
                             "never: require the operator's own serve to be running")
    parser.add_argument("--no-act", action="store_true", help="consider only; execute nothing")
    parser.add_argument("--ready-timeout", type=float, default=None,
                        help="seconds to wait for the owner children (default: demo's own)")
    parser.add_argument("--text", action="store_true",
                        help="print the bounded operator summary instead of raw JSON")
    args = parser.parse_args(argv)
    try:
        report = live_day.run_segment(
            Path(args.instance), duration_seconds=args.duration_seconds,
            max_ticks=args.max_ticks, poll_seconds=args.poll_seconds,
            max_new_requests=args.max_new_requests, act=not args.no_act,
            start_services=args.services, ready_timeout=args.ready_timeout)
    except Exception as exc:  # never echo paths/keys; keep the class name only
        _emit({"ok": False, "status": "error", "reason_code": "LIVE_DAY_RUN_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit(report, _run_text(live_day, report) if args.text else None)
    return int(report.get("exit_code", EXIT_REFUSED))


def cmd_status(argv: list[str]) -> int:
    live_day = _load()
    parser = argparse.ArgumentParser(prog="live_day status")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--live", action="store_true",
                        help="also ask the already-running World owner for one bounded observation")
    parser.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = live_day.status(Path(args.instance), live=bool(args.live))
    except Exception as exc:
        _emit({"ok": False, "status": "error", "reason_code": "LIVE_DAY_STATUS_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit(report, _status_text(live_day, report) if args.text else None)
    return int(report.get("exit_code", EXIT_REFUSED))


def cmd_stop(argv: list[str]) -> int:
    live_day = _load()
    parser = argparse.ArgumentParser(prog="live_day stop")
    parser.add_argument("--instance", required=True)
    args = parser.parse_args(argv)
    try:
        report = live_day.stop(Path(args.instance))
    except Exception as exc:
        _emit({"ok": False, "status": "error", "reason_code": "LIVE_DAY_STOP_FAILED",
               "error_type": type(exc).__name__})
        return EXIT_REFUSED
    _emit(report)
    return int(report.get("exit_code", EXIT_REFUSED))


COMMANDS = {"run": cmd_run, "status": cmd_status, "stop": cmd_stop}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        sys.stderr.write("usage: live_day.py {run,status,stop} ...\n")
        return EXIT_OK if arguments else EXIT_USAGE
    handler = COMMANDS.get(arguments[0])
    if handler is None:
        sys.stderr.write("unknown command: %s\n" % arguments[0])
        return EXIT_USAGE
    try:
        return handler(arguments[1:])
    except SystemExit as exc:  # argparse already printed its own usage
        return int(exc.code or 0)


if __name__ == "__main__":
    sys.exit(main())
