#!/usr/bin/env python3
"""同一恢复用法的后果承接：life 已消费、策略拒绝域保留、Memory 没有因此多写。

这是一个**可重复的操作示例**，不是测试矩阵。全部在本机隔离夹具内：0 产品模型、
0 外网、0 旧实例、0 外发。Decision 与内容回答是脚本替身；真实件是 Supply/World/
Activity/Memory owner、``ProductionLifeRuntime``、AG-0/1/2、AR-0/AR-1、LR-2/LR-5。

流程（一个新隔离根、同一件作品 A、每个阶段各自独立 PID）：

1. **write**（PID 1）：正式 ``evaluate_once`` 写出作品 A；再用适配器既有故障注入让
   owner 写入成功、回执丢失，AR-0 保持 ``UNKNOWN``，Activity 不假完成。另建作品 B。
2. **CLI recover**（PID 2）：``infra_runtime_recovery_cli.py --recover`` 装配真实 runtime、
   取得正规 lease，按原 operation 对账确认成功；版本不增、adapter 提交 0。随后读同一实例：
   - AR-1 结算已产生，LR-5（life）真的消费了它，绑定原 action/settlement/同 Activity；
   - memory/goal/world 是生产 ``RefusingSettlementConsumer`` 的策略拒绝（各自具名记录）；
   - outbox 因这三个策略拒绝域保持 pending，不清零、也不伪装成已全部处理；
   - AG2 经历交接没有因这次恢复新增，Memory 本次进程 accepted=0 只说明“没有新交接可准入”，
     不是 AR1 memory 通知被成功写入了 M0。
3. **再恢复一次**（PID 3）：没有未决动作时明确 ``NOTHING_TO_DO``，不新增结算/outbox/
   投递/checkpoint/经历，M0 记录数不变。
4. **continue**（PID 4）：新 Decision 正式续写同一作品 A，owner 真出新版本；B 不变。

失败路径（owner 不可查询、写者占租约）与“错源拒”放在 ``tests/test_life_recovery_consequences.py``，
不把这个主 demo 重复多遍。

Run (POSIX/WSL)::

    PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
        scripts/life_recovery_consequences_demo.py

成功时打印 ``RESULT: PASS``，并始终打印 ``NEW_ROOT=...`` 方便只核本轮进程。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for entry in (str(ROOT), str(SCRIPT_DIR), str(ROOT / "tests"),
              str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "supply"),
              str(ROOT / "components" / "world" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

CHECKS: list[tuple[str, bool, object]] = []


def check(name: str, condition, detail=None) -> None:
    CHECKS.append((name, bool(condition), detail))
    print("CHECK %-74s %s" % (name, "PASS" if condition else "FAIL"))
    if not condition and detail is not None:
        print("  detail:", json.dumps(detail, ensure_ascii=False, default=str)[:900])


def _state_counts(root: Path) -> dict[str, int]:
    state = Path(root) / "instance" / "life" / "state"

    def _load(name: str) -> dict:
        path = state / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    settlement = _load("result_settlement_state.json")
    progress = _load("activity_progress_state.json")
    integration = _load("life_integration_state.json")
    return {
        "settlements": len(settlement.get("settlements_by_id") or {}),
        "outbox": len(settlement.get("outbox_events") or {}),
        "deliveries": len(settlement.get("deliveries_by_id") or {}),
        "checkpoints": sum(len(rows or []) for rows in
                           (progress.get("checkpoint_updates_by_activity") or {}).values()),
        "consumed": len(progress.get("consumed_evidences_by_id") or {}),
        "handoffs": len(integration.get("handoff_events_by_id") or {}),
        "handoff_deliveries": len(integration.get("handoff_deliveries_by_id") or {}),
    }


def _m0_count(root: Path, cli_env: dict) -> dict:
    """Read the real Memory product seam for this isolated root; never a fake ACCEPT count."""
    from life_memory_service import memory_service_from_env

    env = {key: value for key, value in cli_env.items()
           if key.startswith("NYA02_MEMORY")}
    service = memory_service_from_env(env)
    out = service.list_experiences(limit=100)
    data = out.get("data") or {}
    return {"status": out.get("status"), "count": data.get("count")}


def main(argv: list[str] | None = None) -> int:
    import v05_restart_continuation_smoke as base
    import life_unresolved_recovery_demo as prev

    root = base.prepare_fixture()
    print("NEW_ROOT=%s" % root)
    prev._init_memory(root)
    cli_env = prev._cli_env(root)
    m0_baseline = _m0_count(root, cli_env)

    write = prev._run_phase("write", root, {})
    counts_after_write = _state_counts(root)
    m0_after_write = _m0_count(root, cli_env)
    budget_before_recovery = prev._budget_lines(root)

    rc1, first, stderr1 = prev._recover_with_owner(root)
    counts_after_recover = _state_counts(root)
    m0_after_recover = _m0_count(root, cli_env)
    budget_after_recovery = prev._budget_lines(root)

    rc2, second, stderr2 = prev._recover_with_owner(root)
    counts_after_repeat = _state_counts(root)
    m0_after_repeat = _m0_count(root, cli_env)

    cont = prev._run_phase("continue", root, {
        "artifact_a": write["artifact_a"], "artifact_b": write["artifact_b"]})

    consequences = dict(first.get("consequence_domains") or {})
    events = list(consequences.get("events") or [])
    event1 = next((row for row in events
                   if row.get("action_id") == write.get("action_id")), {})
    domains = dict(event1.get("consequence_domains") or {})
    summary = dict(consequences.get("summary") or {})
    handoff = dict(consequences.get("handoff") or {})
    admission = dict(consequences.get("memory_admission") or {})
    row1 = (first.get("actions") or [{}])[0]

    check("write 阶段真的写出一版作品，随后丢回执的那次也落了一版",
          write.get("versions_after_unknown") == int(write.get("versions_first") or 0) + 1,
          {"versions_first": write.get("versions_first"),
           "versions_after_unknown": write.get("versions_after_unknown")})
    check("丢回执后 AR-0 保持 UNKNOWN，Activity 没有假完成",
          write.get("unknown_outcome") == "STEP_UNKNOWN"
          and write.get("action_status") == "UNKNOWN"
          and (write.get("activity_after_unknown") or {}).get("status") == "ACTIVE",
          {"unknown": write.get("unknown_outcome"),
           "action": write.get("action_status"),
           "activity": write.get("activity_after_unknown")})
    check("CLI --recover 是另一个进程，按原 operation 确认成功且零提交",
          rc1 == 0 and first.get("status") == "RECOVERED"
          and int(first.get("pid") or 0) not in {0, int(write.get("pid") or -1)}
          and row1.get("action_id") == write.get("action_id")
          and row1.get("operation_key") == write.get("operation_key")
          and row1.get("reconciliation_result") == "CONFIRMED_SUCCEEDED"
          and int(first.get("adapter_submit_calls") if first.get("adapter_submit_calls")
                  is not None else -1) == 0,
          {"exit": rc1, "status": first.get("status"), "pid": first.get("pid"),
           "row": row1, "stderr": stderr1})
    check("恢复没有写第二次版本，也没有重置 Activity/预算或租约",
          row1.get("artifact_versions_before") == write.get("versions_after_unknown")
          and row1.get("artifact_versions_after") == write.get("versions_after_unknown")
          and (row1.get("activity") or {}).get("status")
          == (write.get("activity_after_unknown") or {}).get("status")
          and (row1.get("activity") or {}).get("revision")
          == (write.get("activity_after_unknown") or {}).get("revision")
          and budget_after_recovery == budget_before_recovery
          and (first.get("writer") or {}).get("released") is True,
          {"versions": [row1.get("artifact_versions_before"),
                        row1.get("artifact_versions_after")],
           "activity": row1.get("activity"), "budget": [budget_before_recovery,
                                                        budget_after_recovery]})
    check("这次确认的结算真的被 LR-5（life）消费，并绑定原 action/settlement/同 Activity",
          bool(event1.get("settlement_ref"))
          and event1.get("life_consumed") is True
          and event1.get("activity_ref") == write.get("activity_ref")
          and str(event1.get("settlement_ref")) in
          (event1.get("life_consumed_settlement_refs") or []),
          {"event": event1})
    check("life 已消费；memory/goal/world 三域按生产策略各自拒绝，不是漏投",
          domains.get("life") == "CONSUMED"
          and all(domains.get(name) == "POLICY_REFUSED"
                  for name in ("memory", "goal", "world")),
          {"domains": domains, "deliveries": event1.get("deliveries")})
    check("outbox 因策略拒绝域保持 pending，理由指向拒绝而不是“全部完成”",
          int(summary.get("pending_outbox") or 0) >= 1
          and "策略拒绝" in str(summary.get("pending_reason") or ""),
          {"summary": summary})
    check("这次恢复没有新增 AG2 经历交接，也没有因 pending 擅自写 M0",
          int(handoff.get("dispatched_now") or 0) == 0
          and int(consequences.get("handoff", {}).get("delivered") or 0) == 0
          and m0_after_recover == m0_after_write
          and m0_after_recover.get("status") == m0_after_write.get("status"),
          {"handoff": handoff, "m0": [m0_after_write, m0_after_recover]})
    check("Memory 本次 accepted=0 被准确解释为“没有新经历交接可准入”",
          admission.get("status") == "WIRED"
          and int(admission.get("accepted") if admission.get("accepted") is not None else -1) == 0
          and ("没有新的经历交接" in str(admission.get("reason") or "")
               or "上游未生成" in str(admission.get("reason") or "")),
          {"admission": admission})
    check("没有未决动作时再次显式恢复是 NOTHING_TO_DO，不新增结算/投递/checkpoint/经历",
          rc2 == 0 and second.get("status") == "NOTHING_TO_DO"
          and not second.get("actions")
          and int(second.get("pid") or 0) not in {0, int(write.get("pid") or -1),
                                                  int(first.get("pid") or -1)}
          and counts_after_repeat == counts_after_recover
          and m0_after_repeat == m0_after_recover,
          {"exit": rc2, "status": second.get("status"), "pid": second.get("pid"),
           "counts": [counts_after_recover, counts_after_repeat],
           "m0": [m0_after_recover, m0_after_repeat], "stderr": stderr2})
    check("恢复后新 Decision 继续同一作品 A，B 不动",
          cont.get("outcome") == "ACTED" and cont.get("decision") == "CONTINUE"
          and cont.get("versions_after") == int(cont.get("versions_before") or 0) + 1
          and cont.get("activity_ref") == write.get("activity_ref")
          and cont.get("b_head_after") == write.get("b_head")
          and cont.get("b_versions_after") == write.get("b_versions"),
          {"outcome": cont.get("outcome"),
           "versions": [cont.get("versions_before"), cont.get("versions_after")],
           "activity": [write.get("activity_ref"), cont.get("activity_ref")],
           "b": [write.get("b_head"), cont.get("b_head_after")]})

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print()
    print("恢复后果：life 已消费结算=%s；策略拒绝域=%s；outbox 待投递=%s；"
          "AG2 交接 this=%s existing=%s；Memory this accepted=%s；M0 记录 %s -> %s"
          % (summary.get("life_consumed_settlements"),
             ",".join(summary.get("policy_refused_domains") or []) or "无",
             summary.get("pending_outbox"), handoff.get("dispatched_now"),
             handoff.get("existing_handoff_events"), admission.get("accepted"),
             m0_after_write.get("count"), m0_after_recover.get("count")))
    pending_handoffs = (handoff.get("pending_handoff_ids") or [])
    if not pending_handoffs:
        print("可恢复故障用例不适用：本次 Activity 仍 ACTIVE，按合同没有产生待重试经历交接；"
              "不造 handoff 凑场景。")
    print("PIDs: write=%s recover=%s repeat=%s continue=%s (parent %s)" % (
        write.get("pid"), first.get("pid"), second.get("pid"), cont.get("pid"), os.getpid()))
    print("NEW_ROOT=%s" % root)
    for name in failed:
        print("FAILED:", name)
    print("RESULT: %s" % ("FAIL" if failed else "PASS"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
