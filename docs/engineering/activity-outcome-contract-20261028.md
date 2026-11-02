# Activity FAILED / BLOCKED 落地合同（2026-10-28）

## 状态

- `FAILED`：终态。目标范围被 S09 owner 评估为不可达。释放前台；保留 `origin_ref`/`decision_ref`/`adoption_ref`/`checkpoint_ref`/`last_action_ref`/真实已提交版本，以及 `failure_assessment_ref`、`failure_scope_refs`、`failure_criteria_refs`、`failure_evidence_refs`、`unresolved_action_refs`、`failure_assessed_at`；不写 `completion_evidence_ref`。终态默认不复活，重做是新 Activity。
- `BLOCKED`：非终态。当前条件无法推进，不是 PAUSE/PAUSED，也不是外部 WAITING。释放前台；保留 `blocked_on_ref`、`blocked_reason_code`、`blocked_criteria_refs`、`blocked_since`。恢复只走 LR-4 既有 `ResumeCondition`→`ResumeEligibility OPEN`→新 `Decision RESUME`；无 timer、不自动恢复。无条件的 BLOCKED 也由新 `Decision ABANDON` 经 AG-2/LR-2 合法结束（不是自动放弃，不造 OPEN，不改写成 PAUSED）。

## 谁能写

- `BLOCK`：`ACTIVITY_CONSEQUENCE` capability（owner label `s09_suspension_owner`），evidence verifier 重读活动/修订/结构化依据。依据是具名 owner 事实（`block_basis:`/`wait_dep:` 等结构化 ref + reason code），**不是**对条件真值的独立复核；条件满足与否仍只由 LR-4 的 condition 读口判断。
- `FAIL`：`ACTIVITY_CONSEQUENCE` capability（owner label `s09_activity_owner`），evidence verifier 重读活动修订、目标范围、封闭判据名，并逐个调用组合根的 `evidence_resolver`（现有 Supply / AR-0 / AR-1 owner 读）。裸 ref、模型/HTTP 自填 `verified=true`、错 revision、不可读证据一律拒绝；**判据还必须被证据真正支持，且证据必须属于本 Activity**（2026-10-28 收尾）：拒绝不是 FAILED，别件的失败不能给本件签字。
- Action `FAILED`/`UNKNOWN` 本身不触发任何 Activity 转换；迟到结算只改 AR-0/AR-1，不改 Activity 的 FAILED/revision/completion。

## 判据支持矩阵（本版实际支持）

| 判据 | 支持条件 | 明确不支持 |
|---|---|---|
| `ARTIFACT_ARCHIVED` | 重读的 managed-artifact owner 行 `lifecycle=ARCHIVED`，且其 `artifact_id` 按精确 id 属于本 Activity 已绑定目标（`origin_ref`/`subject_refs`/`checkpoint_ref` 等既有 scope） | 别件已归档作品；`contains`/前缀/标题/hash 模糊匹配 |
| `ARTIFACT_TOMBSTONED` | 同上，`lifecycle` 为 `TOMBSTONED`/`TOMBSTONE` | 同上 |
| `EXTERNAL_TERMINAL_REFUSAL` | **本版无可用 owner 事实，具名拒绝**：Action/结算的 `FAILED` 只说明一次尝试失败，现有 owner 记录没有 `terminal_refusal` 这类「目标永不可能」的终局凭据；GATE_OFF、版本 CAS、预算拒绝都不是终局。UNKNOWN/timeout 更不是 | 任何一次 FAILED/UNKNOWN；不假造 policy 凭据、不删安全门 |
| `SOURCE_WITHDRAWN` | 组合根未接来源撤权读口，不能被满足 | 用无关 ref 冒充 |

- `goal_scope_refs` 必须是本 Activity 自己命名的 ref 子集（调用者不能把 B 加进 A 的范围就当作已认领）；多个目标时不任取第一个——证据按本 Activity 全部已绑定 artifact id 精确归属，匹配不到即具名拒。
- `unresolved_action_refs` 必须是**本 Activity** 的真实且仍未决 Action（owner 行的 `activity_ref` 指向本 Activity）；不能借别件的未知 Action 填补本件的未决义务。

## 无条件 BLOCKED 的退出

- 新窄候选 source `BLOCKED_ACTIVITY` 读 LR-2 的 open Activities，只投影 `status=BLOCKED` 的件；AG-1 对该 kind 只给 `ABANDON`（不给 CONTINUE/START/RESUME/PAUSE）。
- 以下情况**具名扣留**而不是假「可选」：Activity 为 `ATOMIC`；AG-2 记录里仍有未结算 Action（partial `ACTION_UNKNOWN_UNSETTLED` 或 AR-0 非 SUCCEEDED/FAILED）；安全事实读不到（provider 缺失/读失败/读不完整）。
- 该候选不抢任何 ACTIVE 前台；有 legacy PAUSED 前台时不给第二个挂起退出。BLOCKED 期间同一目标不会被 PERSONAL_OPPORTUNITY 当成新 START（`paused_target_provider` 已包含 BLOCKED）。
- 下一轮材料：BLOCKED 进入 `materials.has_material` 与 continuity 的 `unfinished`，标 `continue_allowed=false`；无 OPEN 资格时 `next_step=BLOCKED_AWAITING_CONDITION`、choice kind `BLOCKED_SUSPENDED`，有 OPEN 资格时才转成 `RESUME_WAITING_NEW_ELIGIBILITY`。

## 兼容与交接

- 用到新词汇的活动/转换为 `chiyo.life.activity.v2` / `chiyo.life.transition.v2`；v1 记录保持可读、不迁移。只认 v1 的旧 reader 会按未知版本拒绝 v2，不会静默套用旧语义。
- **正式 fail 入口**（`runtime.activity_outcome.fail_activity`，CLI `fail` 走同一入口）在 owner 转换提交成功后立即调用 AG-2 `emit_experience_handoff(kind=FAILED)`，带 assessment/evidence/unresolved refs 与真实 Artifact refs，不带 completion 证据；同一 operation 重放复用原 handoff（LR-2 幂等 + handoff 幂等 id）。崩溃后仍由 AG-2 `reconcile_integration` Phase F 补发。demo 不再手写 emit。
- 交接结果按真实 M0 consumer 的 disposition（ACCEPT/DUPLICATE/DEFER/失败）投影；`lexp` 创建不等于 Memory 已接纳，S05 准入未改。下一轮可读：Memory 行带 `outcome_kind=FAILED` 与真实 artifact；Activity owner 记录与手机读面带 `failure_assessment_ref`/`failure_criteria_refs`/`failure_evidence_refs`/`unresolved_action_refs`（只结构化 ref，不投影正文）。
- 手机读面原样透出 `FAILED`/`BLOCKED` 与 owner 原因码/结构化依据，不投影私人正文。

## 验证入口

```
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/activity_outcome_demo.py'
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_activity_outcome'
```

本地正式入口：`scripts/activity_outcome_cli.py show|fail|block`（写命令需要已 prepare 的 instance，走真实 runtime/lease；`fail` 输出含 `handoff` 实际 disposition）。

## 边界

- demo 的 Decision/内容仍为脚本替身；BLOCK 的触发是 S09 owner 在脚本里记录，不是模型自然选择；UNKNOWN 用例注入的是「真实写入后应答丢失」，不是网络故障矩阵。UNKNOWN receipt 由 demo 通过现有 AR-0 入口写成（claim=UNKNOWN），只为走到语义判据，不是真实网络故障。
- 本 Activity 的目标归属按 owner 已有行重核（artifact 精确 id、Action `activity_ref`），不新增持久格式；admission verifier 与服务层复用同一校验，提交前按 live canonical Activity 再核一次 revision/scope/证据。
- 不证明真实模型自然采用、G1、24h 或跨 PID crash 半写；不把一次 demo 绿冒认全库或生产日常通过。
