# 完成后迟到纠正：冲突可见、终态不偷偷复活（2026-10-29）

## 它现在能做什么

一个已经 `COMPLETED` 的 Activity 收到迟到的、属于它原完成依据的纠正（AR-1
`SETTLED_RESULT_CORRECTION_EVENT`）时：

- LR-5 记一条 `COMPLETION_CONFLICT` / `RECONCILIATION_REQUIRED`，绑定
  `completion_evidence_ref`（原完成链）与 `conflicting_evidence_ref` / `conflicting_settlement_ref`
  （迟到的结算与证据）；
- Activity 仍 `COMPLETED`，revision 不变，前台不重新占用，不新建 Activity、不重放动作、
  不新增作品版本或 Memory 行；
- 同一条纠正重投是幂等重放，不会多出第二条冲突；
- 新进程同根重开后冲突与原完成证据都还在。

指向别的 Activity/结算、或读不到原完成依据的纠正，会在写任何记录之前被具名拒绝。

## 用户/程序怎么查

只读、不建 runtime、不取 lease：

```bash
python -B scripts/activity_outcome_cli.py corrections --instance <实例目录>
python -B scripts/activity_outcome_cli.py corrections --state-root <life/state 目录> \
    --activity-id actv_xxxxxxxx
```

输出里的 `reconciliations` 是完成冲突，`late_terminal_evidence` 是终态上收到的迟到证据，
`activities` 给出该 Activity 的 `status` / `revision` / `completion_evidence_ref`。
快照尚未落盘时读同目录的 `activity_progress_journal.jsonl`，只取这两个视图，不重放、不写。

## 一条完整真实链的演示

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/activity_late_correction_demo.py
```

它会建一个全新隔离根（保留不删），用真 Supply owner 与正式 AG-1/AG-2/AR-0/AR-1/LR-5：

1. A 的既有完成合同（作品 closeout）经正式 `evaluate_once` 真的归档并 `COMPLETED`；
2. AR-1 `settle_action` 收到一条迟到的结构化观测，把原结算改成 `CONFLICT` 并产生纠正事件；
3. 纠正经 outbox 到 LR-5，CLI 能看到冲突；重投幂等；
4. 未知结算、B 的真实事件伪指 A、无关结算的纠正都被具名拒，A/B/前台不变；
5. 关掉自己的 owner 后由**新进程**同根重开，冲突仍在、重投仍幂等。

## 边界

- Decision/内容是脚本替身，0 产品模型调用；迟到观测是结构化替身，不是真实外部审计系统。
- 只覆盖 closeout 这条既有完成合同的真实链，不证明自然模型采用、G1 或 24h。
- 不改变 Memory 准入/撤权、AG-1/AG-2/cycle/AR-0/AR-1 状态机、默认门或预算。
