#!/usr/bin/env python3
"""S08 F-093..F-097 CLI：只读看"未来组织现在是什么"。

它只打印已有记录，不写任何东西、不执行任何事项：

* ``fitem:`` 是**她**提出的延期意向（谁提出 → 哪条已提交 Decision）；
* ``plan:`` 是**她**排进今天的那一条，绑定提出它的 Decision；
* ``oblig:`` 是 **operator** 给的当日义务，她不能改；
* Activity 是已经采用的现在，不是未来事项；
* LR-4 的 Agenda/Eligibility 只是恢复资格，不执行；
* S08 的 Commitment **还没有 owner**：本 CLI 如实报 ``NOT_IMPLEMENTED``，不把任何已有对象
  改名叫承诺。

没有正文/理由/凭据输出。字段不存在打印 ``null``，读失败打印 ``UNAVAILABLE`` + 原因码，
不把故障显示成空表。

Usage::

    python scripts/s08_future_review_cli.py --instance <life-instance>
    python scripts/s08_future_review_cli.py --state-root <instance>/life/state --now 2026-10-27T21:00:00+08:00
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "phone"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "tests"), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_future_organization_review as review  # noqa: E402


def _state_root(args: argparse.Namespace) -> Path:
    if args.state_root:
        return Path(args.state_root)
    return Path(args.instance) / "life" / "state"


def _activities(state_root: Path, day: str) -> tuple[list[dict], dict]:
    """The existing phone-side day read, reused (one day rule, one set of failure codes)."""

    try:
        from components.phone import own_day_read as odr  # noqa: PLC0415
        return odr.read_today_activities(state_root, day)
    except Exception as exc:
        return [], {"state": "unavailable",
                    "reason_code": str(getattr(exc, "reason_code", type(exc).__name__))[:80],
                    "detail": f"{type(exc).__name__}: {exc}"[:160]}


def render_text(envelope: dict) -> str:
    data = envelope.get("data") or {}
    lines = ["S08 future organization  day=%s  now=%s  read_only=%s"
             % (data.get("day"), data.get("now"), data.get("read_only"))]
    lines.append("commitment: %s (%s)"
                 % ((data.get("commitment") or {}).get("state"),
                    (data.get("commitment") or {}).get("reason_code")))
    for name, key in (("later items", "future_items"), ("today plan", "day_plan"),
                      ("operator obligations", "obligations")):
        block = data.get(key) or {}
        lines.append("%s: status=%s count=%s" % (name, block.get("status"),
                                                 block.get("count",
                                                           block.get("open_count"))))
        for row in (block.get("entries") or [])[:8]:
            lines.append("  - %s" % json.dumps(row, ensure_ascii=False, default=str))
    lines.append("activities: status=%s count=%s"
                 % ((data.get("activities") or {}).get("status"),
                    (data.get("activities") or {}).get("source", {}).get("count")))
    agenda = data.get("agenda") or {}
    lines.append("agenda: conditions=%s eligibilities=%s (eligibility is not execution)"
                 % (len(agenda.get("open_conditions") or ()),
                    len(agenda.get("open_eligibilities") or ())))
    for name, src in (data.get("sources") or {}).items():
        lines.append("source %-22s %-12s %s" % (name, src.get("state"),
                                                src.get("reason_code") or ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only S08 view: future items, today plan, obligations, agenda.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--instance", help="Life instance root (uses <instance>/life/state)")
    source.add_argument("--state-root", help="the directory that holds life_future_items.json")
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--now", default=None, help="business instant (ISO); default now")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)

    state_root = _state_root(args)
    if not state_root.exists():
        print(json.dumps({"ok": False, "reason": "STATE_ROOT_NOT_FOUND",
                          "state_root": str(state_root)}, ensure_ascii=False))
        return 2
    envelope = review.build_future_organization_view(
        state_root=state_root, subject_id=args.subject_id, now=args.now)
    day = str((envelope.get("data") or {}).get("day") or "")
    activities, activity_source = _activities(state_root, day)
    # Re-build with the Activity read so the envelope has one shape (the first pass only
    # determined today's business date).
    envelope = review.build_future_organization_view(
        state_root=state_root, subject_id=args.subject_id, now=args.now,
        activities=activities, activity_source=activity_source)
    if args.format == "text":
        print(render_text(envelope))
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2, default=str))
    return 0 if envelope.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
