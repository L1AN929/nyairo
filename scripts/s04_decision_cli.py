#!/usr/bin/env python3
"""S04 F-049/F-050/F-051 本地只读 CLI：一条决定的有限摘要、已记录未选项、Adoption 状态。

用途
====
在**本地**（操作者自己的终端）查一条已提交的 AG-1 ``DecisionRecord``：

* 当时实际选择了哪个候选、候选来自哪里；
* 既有处置表里**真正记录**的未选 / 延期 / 硬拒 / 当前不可执行项与理由代码；
* Decision 与 Adoption 分开：从未采用 / 采纳被拒 / 已采纳，各说各的；
* 记录里没有的字段一律 ``NOT_RECORDED``，不做事后补写。

只读
====
只打开 AG-1 / AG-2 的只读端口（``load_state``），不取 lease、不写 Decision、
不写 Adoption、不写 Activity、不调用模型、不联网。

rationale
=========
默认只报告 ``short_rationale`` 是否存在与长度；正文要显式 ``--show-rationale`` 才输出。
这是本地 CLI 的许可边界，不构成对外公开、也不隔离同 uid 的进程或管理员。

实时适用性
==========
本 CLI 不复制 AG-2 的准入规则，因此不连接正式 runtime 时明确报 ``NOT_EVALUATED``；
按现有准入复核请用 demo / 正式运行链里的 ``validate_decision_for_adoption``。

示例
====
    python scripts/s04_decision_cli.py --instance /tmp/nyairo-inst --list
    python scripts/s04_decision_cli.py --instance /tmp/nyairo-inst --decision-id dec:abc123
    python scripts/s04_decision_cli.py --state-root /tmp/nyairo-inst/life/state --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import decision_review as dr  # noqa: E402


def _state_root(args: argparse.Namespace) -> Path:
    if args.state_root:
        return Path(args.state_root)
    if args.instance:
        import one_day_life as odl
        return Path(odl.life_state_root(args.instance))
    raise SystemExit("需要 --instance 或 --state-root")


def _print_list(rows: list[dict]) -> None:
    if not rows:
        print("没有可读的决定记录。")
        return
    for row in rows:
        if row.get("status"):
            print("读取失败: %s %s" % (row.get("status"), row.get("reason_code")))
            continue
        print("%s  %s  %s%s" % (
            row.get("decision_id"), row.get("created_at"),
            row.get("decision"),
            "" if row.get("decision_route") is None else " (%s)" % row.get("decision_route")))
        if row.get("selected_candidate_ref"):
            print("    选中: %s" % row["selected_candidate_ref"])
        print("    理由代码: %s" % ", ".join(row.get("reason_codes") or [dr.NOT_RECORDED]))
        if row.get("deferred_candidate_refs"):
            print("    延期: %s" % ", ".join(row["deferred_candidate_refs"]))
        if row.get("has_rationale"):
            print("    有简短理由（正文扣留）")


def _print_one(row: dict) -> None:
    if row.get("status") != "OK":
        print("状态: %s" % row.get("status"))
        if row.get("reason_code"):
            print("原因: %s" % row["reason_code"])
        if row.get("note"):
            print(row["note"])
        return
    print("决定: %s" % row.get("decision_id"))
    print("时间: %s" % row.get("created_at"))
    print("类型: %s (%s)" % (row.get("decision"), row.get("decision_route")))
    print("摘要: %s" % row.get("summary"))
    print("理由代码: %s" % ", ".join(row.get("reason_codes") or [dr.NOT_RECORDED]))
    rationale = row.get("rationale") or {}
    if rationale.get("text"):
        print("简短理由: %s" % rationale["text"])
    elif rationale.get("recorded"):
        print("简短理由: 已记录 %d 字（正文扣留，--show-rationale 可本地查看）"
              % int(rationale.get("chars") or 0))
    else:
        print("简短理由: NOT_RECORDED")

    selected = row.get("selected")
    if selected:
        print("选中: %s  kind=%s  source=%s:%s" % (
            selected.get("candidate_ref"), selected.get("candidate_kind"),
            selected.get("source_kind"), selected.get("source_ref")))
    else:
        print("选中: 无（决定=%s）" % row.get("decision"))

    others = row.get("alternatives") or []
    if not others:
        print("未选/延期: NOT_RECORDED（处置表里没有其它候选）")
    else:
        print("未选/延期（只列既有处置表，不推断影响程度）:")
        for item in others:
            print("  - %s  %s  kind=%s  reason=%s" % (
                item.get("candidate_ref"), item.get("disposition"),
                item.get("candidate_kind"), ", ".join(item.get("reason_codes") or [])))

    adoption = row.get("adoption") or {}
    print("Adoption: %s  owner=%s" % (adoption.get("status"), adoption.get("owner")))
    if adoption.get("adoption_id"):
        print("    id=%s  activity=%s  adopted_at=%s" % (
            adoption.get("adoption_id"), adoption.get("activity_ref") or "-",
            adoption.get("adopted_at") or "-"))
    if adoption.get("rejection_reason_code"):
        print("    拒绝原因: %s" % adoption["rejection_reason_code"])
    print("区分: %s（决定是历史；只有 Adoption 产生当前/未来约束）" % row.get("decision_state"))

    live = row.get("live_applicability") or {}
    if live:
        print("实时适用性: %s%s" % (
            live.get("status"),
            "" if not live.get("reason_code") else " (%s)" % live["reason_code"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="S04 决定的只读摘要（Decision / Adoption 分开，未记录项明确标注）")
    parser.add_argument("--instance", help="Life 实例根（与 --state-root 二选一）")
    parser.add_argument("--state-root", help="Life state root（与 --instance 二选一）")
    parser.add_argument("--decision-id", help="单查一条决定")
    parser.add_argument("--list", action="store_true", help="只列最近的骨架（默认单查或列表）")
    parser.add_argument("--limit", type=int, default=10, help="列表条数（1-50，默认 10）")
    parser.add_argument("--show-rationale", action="store_true",
                        help="在本地终端显示 short_rationale 正文（默认扣留）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    state_root = _state_root(args)
    readers = dr.open_readers(state_root)
    decision_read = readers["decision_read"]
    integration_read = readers["integration_read"]

    if args.decision_id:
        row = dr.summarize_decision(
            decision_read, integration_read, args.decision_id,
            include_rationale=bool(args.show_rationale))
        row["live_applicability"] = dr.review_live_applicability(
            decision_read, args.decision_id)
        if args.json:
            print(json.dumps(row, ensure_ascii=False, indent=1))
        else:
            _print_one(row)
        return 0

    rows = dr.list_recent_decisions(decision_read, limit=args.limit)
    if args.json:
        print(json.dumps({"count": len(rows), "decisions": rows},
                         ensure_ascii=False, indent=1))
    else:
        _print_list(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
