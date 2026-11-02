# Agent Note: 完成后迟到纠正接进 LR-5 终态冲突读面

Status: implemented

## Problem

LR-5 早就定义了 `CompletionReconciliationRecord` 与 `_handle_evidence_on_terminal_activity`：
终态 Activity 收到迟到纠正时记 `COMPLETION_CONFLICT` / `RECONCILIATION_REQUIRED`，不重开、
不重放动作。但真实链走不到它：

1. `ActivityResultBinding.allowed_evidence_kinds` 在生产装配里只写
   `["SETTLED_RESULT_EVENT", "ARTIFACT_CANONICAL_STATE"]`（world/daily/body 是
   `WORLD_CANONICAL_OBSERVATION`），而 AR-1 迟到纠正事件的 `source_kind` 是
   `SETTLED_RESULT_CORRECTION_EVENT`。`match_evidence_to_binding` 在 source-kind 一步就把
   纠正判成 `SOURCE_KIND_NOT_ALLOWED_BY_BINDING`，终态处理器永远收不到。
2. 即使放进来，处理器只按“是否 conflicting claim”决定记冲突，没有核对这条纠正
   `supersedes_settlement_ref` 是否真的落在该 Activity 自己的完成依据上，同 subject 的另一笔
   结算可以被当成这次完成的冲突。
3. 用户侧没有读面：`activity_outcome_cli.py show` 只读 canonical Activity 与等待/资格，
   看不到 reconciliation 与 late-terminal 记录；活动运行时状态还可能只在 hash-chained
   journal 里，快照尚未落盘。

## Decision

只改 LR-5 读入侧 + CLI 读面，不新增 correction owner、表或枚举：

- `match_evidence_to_binding`：当绑定允许 `SETTLED_RESULT_EVENT` 时，同时允许该 Action 的
  `SETTLED_RESULT_CORRECTION_EVENT`。target/authority/correlation 仍逐项照旧校验。
- `_completion_basis_settlement_refs`：完成依据从真正消耗掉的
  `ActivityCompletionEligibility.settlement_refs`（以及它引用的已消费 evidence）读取；
  `completed_once_by_activity` / `completion_eligibilities_by_id` 是权威来源，
  Activity 的 `completion_evidence_ref`（通常是 AR-0 `aevid:`）只作次级来源。
- `_correction_relation_rejection`：沿 AR-1 自己的 `supersedes_ref` 链回溯，直到完成结算；
  链上命中才算“关于这次完成”。读不到依据或链不命中分别返回
  `COMPLETION_BASIS_UNVERIFIABLE` / `CORRECTION_TARGETS_UNRELATED_COMPLETION_EVIDENCE`，
  在写任何 conflict 之前拒绝，只进 unbound 审计。
- `scripts/activity_outcome_cli.py corrections`：默认纯读，优先读 owner 快照，快照未落盘时
  只读 journal 的 `act_delta.reconciliations_by_activity` 与 `late_terminal_append`；不建
  runtime、不取 lease、不写文件，只输出 id/status/reason/count。

端到端用法由 `scripts/activity_late_correction_demo.py` 交付：真 Supply owner、
正式 AG-1(脚本 Decision)/AG-2/AR-0/AR-1/LR-5，A 经既有 closeout 完成合同到达 COMPLETED，
AR-1 `settle_action` 用一条迟到结构化观测把原结算改成 CONFLICT 并产生 `scor:` 纠正事件，
经 outbox 送到 LR-5；A 仍 COMPLETED、前台不复活、无新 Activity/版本/Action/Memory；
同纠正重投幂等；新进程同根重开仍见冲突；未知事件、B 的事件伪指 A、无关结算纠正都具名拒。

## Alternatives considered

- **在 AG-2/cycle 的每处 `result_binding_spec` 里补上 correction kind**：最强理由是“绑定
  本来就应该写全”，但那是冻结的 AG-2/cycle 状态机与候选装配，且每新增一种 Activity 形状都要
  再补一次，漏一处就重演同一个不可达；改 LR-5 的读取别名只影响“这个绑定是否接受同一 Action
  的纠正”，不动任何状态机或权限。
- **终态 Activity 收到的任何同 subject 结算都记冲突**：实现最省事，但正是工单禁止的
  “把同 subject 所有证据互换”；会让 B 的结算伪指 A 时也留下冲突记录。
- **为纠正单独建持久表/日志库**：能表达更多元数据，但当前 owner 已有
  `reconciliations_by_activity` 与 `late_terminal_evidence_log`，新建权威会制造第二套
  完成事实，违反“不重造 correction owner”。

## Consequences

- 真实 AR-1 纠正现在能落到 LR-5 终态冲突读面；`COMPLETED` 仍是终态，不重开、不签
  OPEN/RESUME、不重写原 Action 成功或回执，也不会自动产生新 Memory 行。
- 冲突记录与原完成依据的绑定是“完成资格 settlement_refs + 同行链”；如果一条纠正说的是
  “之前那次冲突其实没事”，它仍会作为 late-terminal evidence 记录（非冲突），不改变完成结论。
- 已知边界：迟到观测在 demo 里是结构化替身（不是真实外部审计系统）；只验证 closeout 这条
  既有完成合同，不覆盖其它 Activity 形状的完成；不证明自然模型采用、G1 或 24h；不改变
  Memory 准入/撤权、AG-1/AG-2/cycle/AR-0/AR-1 状态机或默认门。
