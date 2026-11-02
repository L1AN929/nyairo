# Agent Note: S10 请求、尝试、结果与对账是不同事实（F-113～F-122）

Status: implemented

## Problem

AR-0 已经分别持久化 `ActionRecord` / `ActionAttempt` / `ActionReceipt` / `ResultEvidence` /
`ReconciliationRecord`，但没有任何一个用户可读的入口把这些事实分开表达。直接看
`ActionRecord.status` 会把几件事压成一个词：

- `PREPARED` 是「请求已冻结」，既不是 dispatch，也不是「已经发出去」；
- `ActionAttempt.started_at` 只证明本地尝试开始，不证明 provider 收到过请求；
- receipt 的 `ACCEPTED` 不是 `DELIVERED`，更不是用户已经收到；本地草稿/收件箱也不是用户回执；
- 丢答/timeout 是 `UNKNOWN`，不是 `FAILED`；
- 相同内容/hash 只是相容性证据，不能证明这次 Action 造成了变化。

同时要验证的既有机制分散在 AR-0/AR-1/Supply 适配器/cycle 里：同 key 重放、同 key 异
payload、丢答后用原 operation 对账、未决跨重启。缺的是一条把这些既有机制**接成一条可读链**
的入口，而不是新协议。

## Decision

1. **新增纯读投影 `components/life/scripts/action_outcome_review.py`**，把一条 Action 投影成
   请求 / 尝试 / receipt / 观测结果 / 对账五块，并给出诚实派生的 `phase`、`outcome` 与
   `facts`：
   - `PREPARED → REQUESTED_FROZEN`、`OUTCOME_PENDING`、`dispatch_admitted=NOT_RECORDED`
     （AR-0 没有独立的 DispatchAdmitted 记录，不替它补一个）；
   - `attempt.started_at` 只报 `LOCAL_ATTEMPT_RECORDED`；
   - `ACCEPTED`/`DELIVERED`/`READ` 分别投影，`user_received` 默认 `NOT_RECORDED`；
   - `UNKNOWN` 原样保留；`CANCELLED` 在未提交前报 `NOT_EXECUTED`；
   - 只有 `DEFINITE_PRE_SUBMISSION_FAILURE` 尝试才允许 `not_sent=true`；
   - 未记录字段一律 `NOT_RECORDED`，payload 正文/哈希细节不进投影。
2. **新增 CLI `scripts/s10_action_review_cli.py`**（`--instance` 或 `--state-root`，
   `--action-id`、`--limit`、`--format json|text`）。它是纯读：若发现提交 WAL 未回收，
   直接报 `WAL_PENDING_RECOVERY`，不替旧 writer 重放（replay 是写）。
3. **新增隔离演示 `scripts/s10_action_outcomes_demo.py`**，复用既有真实 owner/runtime 夹具
   （`result_method_behavior_smoke.build_chain`、`v05_restart_continuation_smoke`），走正式
   `evaluate_once`：
   - 真实 START → Action `SUCCEEDED`，1 attempt / 1 receipt / 1 result evidence；同 Action 再提交是
     `idempotent_replay`，attempt 与版本数不增；
   - 真实写入但答复丢失 → `UNKNOWN`（不是 `FAILED`）；有序同根重启后仍是 `UNKNOWN`；
     `reconcile_unknown_step` 用**原 owner operation** 对账为 `CONFIRMED_SUCCEEDED`，
     版本数、attempt 数、operation row 都不再增长；
   - AR-0 层：`PREPARED` 零 attempt 不显示为已发；同 key 异 payload 在 prepare 与 submit 都被
     `PayloadTamperError` 拒绝；正确提交后重放零新效果；
   - 另一真实写者用相同内容写入后，受控 transport seam 不返回 mapping，AR-0 连续两次对账仍是
     `STILL_UNKNOWN`，零 evidence、零重发；hash 相同不被认领为本次 Action 的成功；
   - 具名缺口：AR-0 没有 child/DAG/PARTIAL 结果向量，也没有 `QUIESCED_UNRESOLVED` 或
     bounded query scheduler，输出 `NOT_IMPLEMENTED`/STOP，不新建平行 store 或私有 timer。
4. 不修改 Action/Attempt/receipt 持久字段、幂等身份契约、retry/取消规则、查询政策、AG-1 输出、
   Memory/删除权威、Body/S03/S07/Activity 终态；未新增 owner/库/配置/权限。

## Alternatives considered

- **直接在 `ActionRecord.status` 上加 `phase`/`outcome` 字段**：最强理由是读面最省事；否决，
  这会改 AR-0 持久格式和状态机，属于新协议，且与设计「phase/outcome 独立」是两回事。
- **在手机 `/today` 增加新的公开 action 路由**：最强理由是用户不用开终端；否决，本组先交 CLI，
  公开 HTTP 面要单独评审字段与敏感性，不在本单扩。
- **给查询加 delaysMs/maxQueries/QUIESCED 计时器**：最强理由是更接近设计 Q14；否决，仓库没有
  这套 policy/调度，添加就是新协议与自动重试语义；本单只演示既有的一次确定性对账并具名 STOP。
- **用 content hash 相等直接判成功**：否决，设计 Q13 明确 hash 只是相容性证据，会虚构贡献并忽略
  ABA；演示里另一个真实写者写了同样内容，AR-0 仍保持 `UNKNOWN`。

## Consequences

- 用户/操作者可以只读地看到「请求冻结、有没有尝试、有没有 owner receipt、有没有观测效果、
  有没有对账」以及每一块的真实计数；`PREPARED`、`ACCEPTED`、本地收件箱不再被误读。
- `UNKNOWN` 跨有序重启保持，且只用原 operation 对账；对账不写第二个版本、不增 attempt。
- 未实现/未定义的协议被具名为 `NOT_IMPLEMENTED`/STOP，而不是用空实现或私有重试凑绿。
- 边界：Decision/内容仍是脚本替身；未证真实模型自然采用、G1、24h、crash 矩阵；重启用例是
  有序停启（shutdown → drain → 现有 socket guard），不是强杀半写；World 未启动。
- 相关：[UNKNOWN 闭环与事实读](2026-10-18-world-daily-unknown-closed-loop-and-fact-read.md)、
  [预提交拒绝形状](2026-10-24-world-dispatch-definite-pre-submit.md)、
  [BODY 动作身份](2026-10-25-body-action-decision-identity.md)、
  [S09 持续活动](2026-10-27-s09-continuation.md)。

## Evidence

- `scripts/s10_action_outcomes_demo.py`（真实 Supply/AR-0/AR-1/正式 `evaluate_once` + CLI 子进程）。
- `tests/test_s10_action_outcomes.py`（投影规则、WAL 拒绝、具名缺口）。
- 交付摘要：`.agents/worker-runs/20261027-s10-action-outcomes-result.md`。
