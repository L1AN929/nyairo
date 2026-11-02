#!/usr/bin/env python3
"""运行健康只读入口：把「安静 / 已停 / 待处理积压」当三种事实分开报，不补剧情。

    python3 scripts/runtime_health_cli.py --instance /root/nyairo-day-...
    python3 scripts/runtime_health_cli.py --state-root <instance>/life/state [--json]

它只读：运行记录（resident_summary.json）、覆盖账本（resident_coverage.json）、当前主机
``/proc`` 里该进程的启动时间，以及 AR-0 的动作状态文件（直接 JSON，不走会重放 WAL 的
``load_state``）。不取 lease、不恢复 WAL、不重试/消费/重发、不写任何文件、不启动运行时。
跨主机、缺身份或本平台没有 ``/proc`` 时如实报 UNKNOWN，不当成活着或已停。

退出码：0 = 三类确定事实（安静/安静有待处理/已停）
        3 = 记录还写着 running 但进程已不在（旧记录残留）
        4 = 无法判断（没有记录，或进程身份无法核验）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import runtime_health_review as health_review  # noqa: E402

EXIT_SETTLED = 0
EXIT_STALE = 3
EXIT_UNKNOWN = 4


def _exit_code(verdict: str) -> int:
    if verdict in {health_review.VERDICT_ALIVE_QUIET,
                   health_review.VERDICT_ALIVE_PENDING,
                   health_review.VERDICT_STOPPED}:
        return EXIT_SETTLED
    if verdict == health_review.VERDICT_RECORD_STALE:
        return EXIT_STALE
    return EXIT_UNKNOWN


def _text(result: dict) -> str:
    recorded = dict(result.get("recorded") or {})
    process = dict(result.get("process") or {})
    progress = dict(result.get("progress") or {})
    queue = dict(result.get("queue") or {})
    lines = ["运行健康（只读）：%s" % result.get("instance_state_root")]
    lines.append("结论：%s —— %s" % (result.get("verdict"), result.get("explain")))

    if recorded.get("present"):
        lines.append("记录：running=%s pid=%s started=%s last_tick=%s ticks=%s starts=%s" % (
            recorded.get("running_claim"), recorded.get("pid"), recorded.get("started_at"),
            recorded.get("last_tick_at"), recorded.get("ticks"), recorded.get("starts")))
        if recorded.get("stopped_at"):
            lines.append("  记录里的停止时间：%s" % recorded.get("stopped_at"))
    else:
        lines.append("记录：没有（%s）" % recorded.get("reason"))

    lines.append("进程：%s（%s）%s" % (
        process.get("status"), process.get("basis"), ("，" + process["note"])
        if process.get("note") else ""))
    lines.append("  记录身份 host=%s pid=%s；本机核验 host=%s" % (
        process.get("recorded_host"), process.get("pid"), process.get("checked_host")))

    if progress.get("since_last_tick_seconds") is not None:
        lines.append("最近进度：tick %s（%.0f 秒前）；heartbeat %s" % (
            progress.get("last_tick_at"), progress.get("since_last_tick_seconds"),
            progress.get("last_heartbeat_at") or "-"))
    else:
        lines.append("最近进度：记录里没有 tick 时间（%s）" % progress.get("verdict"))

    lines.append("队列：%s（未确认提交意图=%s，待对账动作=%s，revision=%s）" % (
        queue.get("verdict"), queue.get("open_submission_intents"),
        queue.get("unresolved_actions"), queue.get("revision")))
    failure = queue.get("recent_failure")
    if failure:
        lines.append("  最近明确失败：%s%s（%s）" % (
            failure.get("status"),
            (" / " + failure["error_class"]) if failure.get("error_class") else "",
            failure.get("submitted_at") or failure.get("started_at") or "-"))
    lines.append("  队列说明：%s" % queue.get("note"))

    coverage = dict(recorded.get("coverage") or {})
    if coverage:
        def _shown(value):
            return "-" if value is None else value

        lines.append("覆盖账本：进程数=%s 已证明在线=%s 秒 开放尾=%s 秒 未知=%s 秒 连续=%s" % (
            _shown(coverage.get("process_count")), _shown(coverage.get("covered_seconds")),
            _shown(coverage.get("open_tail_seconds")), _shown(coverage.get("unknown_seconds")),
            _shown(coverage.get("continuous"))))
    for item in result.get("cannot_judge") or []:
        lines.append("不能判断：%s" % item)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读运行健康：安静/已停/待处理积压分开报")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instance", help="one-day 实例根（读取 <root>/life/state）")
    group.add_argument("--state-root", help="直接指定 Life state 根")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON，而不是文本摘要")
    args = parser.parse_args(argv)

    state_root = (Path(args.state_root) if args.state_root
                  else health_review.state_root_for(args.instance))
    try:
        result = health_review.health(state_root)
    except Exception as exc:  # a reader failure is reported as one, not as a verdict
        print(json.dumps({"ok": False, "reason_code": "HEALTH_READ_FAILED",
                          "detail": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        return EXIT_UNKNOWN

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        print(_text(result))
    return _exit_code(str(result.get("verdict")))


if __name__ == "__main__":
    raise SystemExit(main())
