# Agent Note: 有来源后果、分域处理与三轴结算（INFRA F155～F160）

Status: implemented

## Problem

设计 VIII.7 要求「统一运输 + 分散裁定 + 派生总体状态」：Action outcome、Activity lifecycle、
envelope closure 必须分别表达，NO_CHANGE 必须有目标 owner 的可核验 receipt，UNKNOWN 保持
未决。仓库里已经有一条真实链（AR-0 Action/Attempt/Receipt → AR-1 ResultSettlementRecord +
outbox → LR-5 / AG-2 经历交接 → M0），但没有任何用户可读入口把这些事实分开表达：

- 直接看 `ActionRecord.status` 或 `settlement_status=SETTLED` 会把「结果已结算」误读成
  「生活后果已经完成」；
- AR-1 的 consumer disposition（ACCEPT/IGNORE/DEFER/REJECT/CONFLICT）只是注册 consumer
  的回应，不是设计里的 target-owner receipt；
- 生产的 `memory` settlement consumer 是 `RefusingSettlementConsumer`（按策略拒写），
  而 Memory 的真实后果走 AG-2 的 `lexp:*` 经历交接进 M0，两者经常被混为一谈；
- 活动仍在 ACTIVE、经历交接 FAILED_PENDING_RETRY 时，没有任何读面会说
  「保存成功，但不是所有记录域都已处理」。

同时必须先确认一个事实：设计 VIII.7 的 `ConsequenceEnvelope`、确定性 obligation compiler、
required/conditional/advisory、NOT_APPLICABLE 与 owner NO_CHANGE evaluator、depth/capacity
策略在本仓库**都不存在**。AR-1 的 policy registry 只是按 action_kind 的领域结算规则，
不是 obligation compiler。

## Decision

1. **新增纯读投影 `components/life/scripts/infra_settlement_review.py`**，把一次 Action
   投影成三条独立轴：
   - `action_outcome`：AR-0 的 project_action（复用 S10 的 `action_outcome_review`）+
     AR-1 的 settlement（effect claims、status、outcome_kind、history）；
   - `activity_lifecycle`：LR-2 的 canonical Activity + LR-5 自己的 progress 读面；
   - `consequence_envelope`：固定 `NOT_IMPLEMENTED`，并列出缺口名字；AR-1 被明确标为
     `AR1_RESULT_SETTLEMENT_NOTIFICATION_FANOUT`，不改名成 envelope。
2. **分域状态各自由该域的证据决定**：
   - `domains` 列 AR-1 每条 delivery 的原始 status/disposition/attempt，并固定
     `receipt_kind=AR1_CONSUMER_ACK`、`owner_receipt_verified=False`；
   - `life` 域只有在 LR-5 真的消费了带这次 `settlement_ref` 的证据（或 checkpoint update
     的 settlement_refs 含它）时才标 `VERIFIED_BY_OWNER_READ`；
   - `handoffs` 列与该 Action/ settlement 绑定的 AG-2 经历交接，Memory 记录通过既有
     `LifeMemoryService` 读面按 `source_ref` 精确核实；无绑定时报 `UNVERIFIED`；
   - `consequence_domains` 汇总 life/artifact/memory 三个真实后果域，与 AR-1 通知状态
     （`ar1_notification_delivery_complete`）分开。
3. **新增 CLI `scripts/infra_settlement_review_cli.py`**（`--instance` / `--state-root`、
   `--action-id`、`--limit`、`--format json|text`、可选 Memory 绑定参数）。纯读：发现
   `action_reality.wal` / `result_settlement.wal` / `activity_progress.wal` /
   `commit_intent.json` 未回收时直接报 `COMMIT_WAL_PENDING_RECOVERY`，不重放。
4. **新增隔离演示 `scripts/infra_consequence_settlement_demo.py`**，复用
   `result_method_behavior_smoke.build_chain` 的真实 Supply / runtime / AG-1/AG-2 /
   AR-0/AR-1 / LR-2/LR-5 / M0：
   - 正式 `evaluate_once` START → 真实版本、`SETTLED/SUCCESS_EFFECT`；Activity 仍 ACTIVE；
   - 第二次 CONTINUE → 新 Action、新 settlement、新 `lexp:result:` 交接，不被同一
     Activity/root 吞掉；
   - 第三次写入真实成功，Memory 经历交接受控故障（`FlakyExperienceConsumer` 在真实
     consumer 之前抛错，不是第二个 owner）：交接 `FAILED_PENDING_RETRY`、M0 不可召回，
     复核写 `consequence_domains_processed=false`、envelope 不可宣称关闭；
   - 同根有序停启（runtime close → 同根重装配）后状态不丢，成功 Action/版本/attempt
     不重放，未结交接仍挂起；
   - 恢复后走既有 `dispatch_experience_handoffs` 重投原 handoff：同一身份、一次新记录；
     再投是 M0 存储幂等 `DUPLICATE`（明确不是语义 NO_CHANGE）；
   - AR-1 同源重投：`settle_action` idempotent replay，不新增 settlement/事件；同一
     outbox event 重投给 LR-5 不新增 checkpoint。
5. **不修改任何业务源码、持久格式、owner 合同、Memory 准入或删除权威**。整套改动只读
   加新文件；写集外没有碰 `result_method_behavior_smoke.py`、canonical db/contract、
   AG-1 输出、S03/S07/S08/S09/Body、settings 或旧实例。

## Alternatives considered

- **给 AR-1 加 `ConsequenceEnvelope`/obligation 表并让 demo 跑绿**：最强理由是能直接对上
  设计字段名；否决，这是新协议 + 新持久格式 + 新 owner/合规语义，任务明确禁止，且
  「只填状态的假 handler」正是要避免的假绿。
- **把 AR-1 的 consumer ACCEPT 当作 target-owner receipt**：最强理由是省掉 AG-2/M0 的
  关联；否决，AR-1 的 consumer 是 sandbox/拒绝实现，`downstream_proposal_ref` 不是 owner
  可核验回执，用它关闭 envelope 就是 design 明令禁止的「调用者自填 receipt」。
- **把生产 `memory` 通知失败算作 Memory 后果失败**：否决，二者是不同事实。生产策略
  拒写该通知，但真实 Memory 后果由 AG-2 交接与 M0 决定；读面把两者分开，既不掩盖通知
  失败，也不把 M0 成功说成 AR-1 的功劳。
- **在 CLI 里自动重投 FAILED 的后果**：否决，重投会写；恢复必须走实例自己的 runtime 与
  既有 `dispatch_experience_handoffs`，读口只报告、不修复。

## Consequences

- 用户可以只读地回答「动作效果是什么、Activity 怎么样、哪些后果域已处理/阻塞」，
  并且 `SETTLED` 不再被当成生活后果完成；`NOT_IMPLEMENTED` 的协议缺口有名字。
- 分域失败/挂起会阻止「所有后果已处理」的结论；恢复只复用既有重投接口与原身份，
  M0 幂等由 M0 自己回答。
- 边界：Decision/内容是脚本替身；没有真实模型自然采用、G1、24h、crash 矩阵；重启是
  有序停启。设计 VIII.7 的 envelope/compiler/NOT_APPLICABLE/owner NO_CHANGE/
  depth-capacity 仍是未实现缺口，本单不承诺补齐。
- 相关：[S10 请求/尝试/结果/对账](2026-10-27-s10-action-outcomes.md)、
  [S05 Memory 可靠性](2026-10-27-s05-memory-reliability-review.md)、
  [World 后果文案接线](../architecture/2026-10-18-world-daily-world-consequence-wording-and-wiring.md)。

## Evidence

- `components/life/scripts/infra_settlement_review.py`
- `scripts/infra_settlement_review_cli.py`
- `scripts/infra_consequence_settlement_demo.py`（21 checks，exit 0）
- `tests/test_infra_consequence_settlement.py`（12 例）
- 交付摘要：`.agents/worker-runs/20261027-infra-consequence-settlement-result.md`
