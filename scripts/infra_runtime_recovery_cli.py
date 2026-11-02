#!/usr/bin/env python3
"""Recovery review (default, read-only) and explicit recovery (``--recover``).

    python3 scripts/infra_runtime_recovery_cli.py --instance /root/nyairo-day-... [--json]
    python3 scripts/infra_runtime_recovery_cli.py --instance <root> \
        --supply-socket <sock> --subject <subject>
    python3 scripts/infra_runtime_recovery_cli.py --instance <root> --recover [--json]

Without ``--recover`` it answers one question: *can this root be assembled now, does it need
the existing recovery entry, is reading blocked, or must a person stop something first?*  It
never acquires the writer lease, never replays a WAL, never starts a runtime and never writes.

With ``--recover`` it performs the explicit, operator-requested recovery: it assembles the
existing production runtime on this instance (taking the real lease), runs the existing
``recover_all_and_reconcile`` and AR-0's own ``reconcile_action`` for anything still UNKNOWN
(original operation only -- no submit, no new key, no model call), then closes and releases.
The owner bindings come from the instance itself unless overridden here.

Exit codes (review): 0 = actionable (ASSEMBLABLE or NEEDS_EXISTING_RECOVERY),
                     3 = a person must act (MANUAL / READ_BLOCKED),
                     4 = the files do not decide (UNKNOWN).
Exit codes (recover): 0 = RECOVERED or NOTHING_TO_DO,
                      3 = BLOCKED (another writer, missing binding, unreadable state),
                      4 = something is still unresolved after the attempt (an UNKNOWN
                          action, or an experience handoff left deferred /
                          FAILED_PENDING_RETRY in its original pending list).
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

import runtime_recovery_review as review  # noqa: E402

EXIT_ACTIONABLE = 0
EXIT_MANUAL = 3
EXIT_UNKNOWN = 4


def _class_exit(klass: str) -> int:
    if klass in {review.CLASS_ASSEMBLABLE, review.CLASS_NEEDS_RECOVERY}:
        return EXIT_ACTIONABLE
    if klass in {review.CLASS_MANUAL, review.CLASS_READ_BLOCKED}:
        return EXIT_MANUAL
    return EXIT_UNKNOWN


def _recover_exit(status: str) -> int:
    if status in {review.STATUS_RECOVERED, review.STATUS_NOTHING_TO_DO}:
        return EXIT_ACTIONABLE
    if status == review.STATUS_BLOCKED:
        return EXIT_MANUAL
    return EXIT_UNKNOWN


def _text(result: dict) -> str:
    lines = ["恢复只读检查：%s" % result.get("instance")]
    lines.append("结论：%s（%s）" % (result.get("class"),
                                   ", ".join(result.get("reasons") or []) or "无待处理项"))
    writer = dict(result.get("writer") or {})
    lines.append("写者：%s（%s）" % (writer.get("status"), writer.get("reason") or "-"))
    if writer.get("pid"):
        lines.append("  标记 pid=%s instance=%s" % (writer.get("pid"),
                                                   writer.get("instance_id")))
    wal = dict(result.get("wal") or {})
    if wal.get("present"):
        for row in wal.get("rows") or []:
            lines.append("WAL：%s %s（journal=%s revision=%s）" % (
                row.get("store"), row.get("status"), row.get("journal_entries"),
                row.get("store_revision")))
    else:
        lines.append("WAL：没有未处理的提交意图文件")
    actions = dict(result.get("actions") or {})
    unresolved = list(actions.get("unresolved") or [])
    lines.append("未决动作：%d" % len(unresolved))
    for row in unresolved[:5]:
        lines.append("  %s status=%s fence=%s" % (
            row.get("action_id"), row.get("status"), row.get("open_submission_fence")))
    operations = dict(result.get("owner_operations") or {})
    if operations.get("status") == "QUERIED":
        for row in operations.get("rows") or []:
            lines.append("owner 操作：%s -> %s" % (row.get("operation_key"), row.get("result")))
    elif operations.get("status") not in (None, "NOTHING_TO_QUERY"):
        lines.append("owner 操作：%s（%s）" % (operations.get("status"),
                                              operations.get("reason") or "-"))
    config = dict(result.get("config") or {})
    lines.append("模型配置：%s（provider=%s model=%s，未发请求）" % (
        "可用" if config.get("usable_for_requests") else "不完整",
        config.get("provider") or "-", config.get("model") or "-"))
    gates = dict(result.get("gates") or {})
    lines.append("备份恢复：%s；迁移：%s" % (
        (gates.get("backup_restore") or {}).get("status"),
        (gates.get("migration") or {}).get("status")))
    entry = dict(result.get("next_legal_entry") or {})
    lines.append("下一合法入口：%s" % entry.get("entry"))
    lines.append("  %s" % entry.get("action"))
    if result.get("unevaluated"):
        lines.append("未评估：%s" % "；".join(str(item) for item in result["unevaluated"]))
    return "\n".join(lines)


def _recover_text(result: dict) -> str:
    lines = ["显式恢复：%s" % result.get("instance")]
    lines.append("结论：%s" % result.get("status"))
    if result.get("reason"):
        lines.append("原因：%s" % result.get("reason"))
        if result.get("missing"):
            lines.append("  缺少绑定：%s" % ", ".join(str(item) for item in result["missing"]))
        if result.get("detail"):
            lines.append("  详情：%s" % str(result.get("detail"))[:400])
    writer = dict(result.get("writer") or {})
    if writer:
        lines.append("写者租约：已取得=%s 已释放=%s" % (writer.get("lease_acquired"),
                                                      writer.get("released")))
    if result.get("adapter_submit_calls") is not None:
        lines.append("本次 adapter 提交次数：%s（恢复不重发）"
                     % result.get("adapter_submit_calls"))
    for row in result.get("actions") or []:
        lines.append("动作：%s op=%s %s -> %s（对账=%s，投递=%s，版本 %s -> %s）" % (
            row.get("action_id"), row.get("operation_key") or "-",
            row.get("status_before"), row.get("status_after"),
            row.get("reconciliation_result") or "-", row.get("result_delivery"),
            row.get("artifact_versions_before"), row.get("artifact_versions_after")))
        activity = dict(row.get("activity") or {})
        if activity:
            lines.append("  Activity：%s status=%s revision=%s" % (
                activity.get("activity_id"), activity.get("status"),
                activity.get("revision")))
    delivery = dict(result.get("result_delivery") or {})
    if delivery:
        lines.append("结果投递：已交付=%s 延迟=%s 待重试=%s 待处理handoff=%s" % (
            delivery.get("delivered"), delivery.get("deferred"),
            delivery.get("failed_pending_retry"),
            len(delivery.get("pending_handoff_ids") or [])))
        if delivery.get("pending_handoff_ids"):
            lines.append("  待重试交接：%s" % ", ".join(
                str(item) for item in delivery["pending_handoff_ids"][:5]))
    consequence = dict(result.get("consequence_delivery") or {})
    if consequence:
        lines.append("后果投递：结算=%s outbox=%s 待投递=%s 冲突=%s" % (
            consequence.get("settlements"), consequence.get("outbox_events"),
            consequence.get("pending_outbox"), consequence.get("conflicts")))
    memory = dict(result.get("memory") or {})
    if memory:
        lines.append("Memory：%s（accepted=%s deferred=%s ignored=%s）" % (
            memory.get("status"), memory.get("accepted"), memory.get("deferred"),
            memory.get("ignored")))
    consequences = dict(result.get("consequence_domains") or {})
    if consequences:
        summary = dict(consequences.get("summary") or {})
        lines.append("后果分域：life 已消费结算=%s；策略拒绝域=%s；失败域=%s；outbox 待投递=%s" % (
            summary.get("life_consumed_settlements"),
            ", ".join(summary.get("policy_refused_domains") or []) or "无",
            ", ".join(summary.get("failed_domains") or []) or "无",
            summary.get("pending_outbox")))
        if summary.get("pending_reason"):
            lines.append("  %s" % summary["pending_reason"])
        for event in consequences.get("events") or []:
            lines.append("  事件 settlement=%s action=%s activity=%s outcome=%s" % (
                event.get("settlement_ref"), event.get("action_id"),
                event.get("activity_ref"), event.get("outcome_kind")))
            lines.append("    life 已消费=%s；域状态=%s" % (
                event.get("life_consumed"),
                ", ".join("%s:%s" % (domain, state) for domain, state
                          in sorted((event.get("consequence_domains") or {}).items()))
                or "无记录"))
        handoff = dict(consequences.get("handoff") or {})
        lines.append("AG2 经历交接：既有=%s 本次投递=%s 交付=%s 待重试=%s；%s" % (
            handoff.get("existing_handoff_events"), handoff.get("dispatched_now"),
            handoff.get("delivered"),
            handoff.get("failed_pending_retry"), handoff.get("reason") or "-"))
        admission = dict(consequences.get("memory_admission") or {})
        lines.append("Memory 准入：%s accepted=%s；%s" % (
            admission.get("status"), admission.get("accepted"),
            admission.get("reason") or "-"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Life runtime recovery review (read-only by default)")
    parser.add_argument("--instance", required=True, help="instance root (contains instance.json)")
    parser.add_argument("--supply-socket", default=None,
                        help="optional Life Supply owner socket (review lookup / recovery owner)")
    parser.add_argument("--subject", default=None, help="owner subject for the operation lookup")
    parser.add_argument("--recover", action="store_true",
                        help="explicitly run the existing recovery entry (takes the real lease)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.recover:
        result = review.recover(args.instance, supply_socket=args.supply_socket,
                                subject=args.subject)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(_recover_text(result))
        return _recover_exit(str(result.get("status")))

    result = review.review(args.instance, supply_socket=args.supply_socket,
                           subject=args.subject)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(_text(result))
    return _class_exit(str(result.get("class")))


if __name__ == "__main__":
    raise SystemExit(main())
