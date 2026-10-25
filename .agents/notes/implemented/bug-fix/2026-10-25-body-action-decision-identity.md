# Agent Note: BODY 动作身份对齐已提交 Decision，而不是观测坐标

Status: implemented

## Problem

`l` 的真实短日里出现了六个不同的 `START` Decision、六个不同的 Body Activity，却在正式
日志里共用同一个 `actn:`（同一个 `step_spec_id`、同一个 `activity_ref`/`decision_ref`），
累计六次 `PRE_SUBMIT_FAILED / GATE_OFF`、零 World 回执。对照现树，这不是"同一次执行的
幂等重试"，而是上游身份表达缺口：

* `autonomous_life_cycle._world_step_spec` 的 `bodyact:` 分支只把 `place` / `revision` /
  `target_ref` 交给 builder，**没有传已提交的 `decision_id`**；
* `life_body_action_targets.build_body_action_step` 的 key 就是
  `idem_step:v02:world:bodyact:<place>:r<revision>:<target>`，只描述"她站在哪、看到哪一版、
  做什么"，不描述"这是哪个 Decision 决定的"；
* AG-2 用 `step_idem` 派生 `step_spec_id`，AR-0 再用 `ar0_act:<step_spec_id>` 派生 `action id`；
  AR-0 的幂等索引命中旧 Action 时直接返回旧记录，新 `decision_ref` / `activity_ref` 被丢弃。

同一 target 在同一个 place/revision 下被两个不同 Decision 选中时，这两次逻辑动作因此
无法各自拥有 step/Action/授权。反过来，同一个 Decision 的重试与 UNKNOWN 对账又必须
保持原执行——观测坐标表达不了这个区别。`dailyact:` 侧早已按"动作身份属于已提交 Decision"
处理，BODY 是漏接，不是另一套规则。

限定说明：key 相同同时要求 target 相同，所以这里证明的是**归属与执行身份被第一次创建
冻结**，不是"提交了另一个 World 对象"；gate off 的零效果也不能反过来证明开闸后安全。

## Decision

把正式 BODY 路径的身份补齐到与 `dailyact:` 同一原则：

1. `AutonomousLifeCycle._world_step_spec` 的 `bodyact:` 分支读取已提交 DecisionRecord 的
   `decision_id`，作为 `decision_ref` 传给同一个 builder；目标仍来自该 Decision 自己选中的
   候选记录，不重新猜。
2. `build_body_action_step(ref, *, place, revision, decision_ref=None)`：
   * 带 `decision_ref` 时 key =
     `idem_step:v02:world:bodyact:decision:<quoted decision_id>:<编码原串>`。**不包含**
     place/revision，因此同一 Decision 的重试、丢答对账不会被随后变化的观测拆成新执行；
     不同 Decision 即使 place/revision/target 完全相同也得到不同 key。
   * 不带 `decision_ref` 时保留原
     `idem_step:v02:world:bodyact:<place>:r<revision>:<编码原串>`。这只是手工调用的兼容
     fallback，不是正式安全保证：两个独立的无 Decision 调用者若观测相同，仍会按旧语义合并。
3. 不改 AG-2 的 step 幂等语义、AR-0 的幂等索引 / Action 状态矩阵、LR-2/LR-4、Body 阈值、
   World owner、默认 gate、预算或模型；不新增自动重发、自动完成/退出、多步 BODY、资格或
   动词；BODY readiness 撤 `CONTINUE` 的既有结论保持不变；不迁移、不重放历史实例。

`payload_spec` 仍是 `{verb, target, expected_dependencies}`，`target_ref`、效果谓词、
`POST_SUBMIT_KEEP_ACTIVE`、`sleep_like` 拒绝等编码/准入边界全部不变，只是 key 多了身份。

## 验收分层与父级独立复跑

父级复跑：`tests.test_body_action_identity` + `tests.test_day_life_body_actions` +
`tests.test_body_single_action_readiness` 共 35 例 2.297 秒 OK（A_EXIT=0）；
`scripts/body_action_identity_smoke.py` 输出 `SMOKE_OK`（B_EXIT=0）；
日志 `20261025-body-action-identity-parent-ab.log`。worker 自述不替代这次复跑。

两层证据的边界必须分别读：

- **第 1/2 项走正式链**：生产 assembly → `evaluate_once` → AG-1 原始 JSON 脚本 Decision →
  真实 Activity / AR-0，真 Supply 与 World gateway，gate off。同一 Decision 同命令重试落在
  同一 step/Action（attempt 1→2）；新 Decision 在同一 bodyact 目标、同一 world revision 1 下
  创建独立的 Decision/Activity/step/Action/授权，旧 attempt 保持 2、新 attempt 1。
- **第 3/4 项在隔离 AR-0 + 真实 World owner 上验**，不经过 Life pre-submit 准入：可控丢答让
  真实 POSE 已执行而 Action 为 UNKNOWN，裸重试抛 `UnknownActionRetryForbiddenError` 且提交
  次数仍为 1；原 execution 经 reconcile 确认 SUCCEEDED、owner revision 未增；新 Decision 同
  target 产生新 Action 且不 `idempotent_replay`；第三个 Decision 另做 POSE 真实改变基底。

因此**不能**把第 3/4 项读成“完整 Life gate-on 的成功回执 Activity 归属已验”。gate off 的
零效果也不能反过来证明开闸后安全。隔离 AR-0 层使用明确脚本 Decision ID，不冒 AG-1 提交全链。

无 Decision 的手工调用保留旧 fallback，只是兼容，不提供正式身份保证；后续调用方应继续传
Decision。本单不新增多步 BODY、自动重试、自动完成/退出或迁移，BODY readiness 撤 `CONTINUE`
的既有 STOP 原因原样保留（见 [BODY 单动作 readiness](2026-10-25-body-single-action-readiness.md)）。

## Alternatives considered

* **用观测状态哈希（place + revision + pose/hands…）当 key**：否决。最强理由是它能自动
  区分"世界看起来不同"，实现也最小；但 `dailyact:` 的既有论证逐条成立：状态回到相同字节时
  它会重建旧 key、把第一次的 SUCCEEDED 回执当成新动作的答案；而同一 Decision 提交成功却
  丢答、随后观测变化时，它又会造出第二个动作。观测是"决定发生在什么情境"，不是动作身份。
* **在 AG-2 或 AR-0 层加"activity 不同就当新 step/Action"**：否决。那是把身份规则塞进
  幂等 owner 内部，改变 AG-2 step 语义与 AR-0 状态矩阵，还要求 owner 读懂上游 activity；
  AR-0 对相同 key 返回同一执行本身是正确的，缺的是上游 key 没有表达清楚。
* **每轮时间戳 / 随机 nonce 当 key**：否决。它能让每次 START 都"不同"，但同一 Decision 的
  合法重试和 UNKNOWN 对账也会变成新执行，直接破坏 AR-0 提交围栏。
* **迁移 / 重放 l 的六个 Action，或把它们合并成一个**：否决。历史实例不重写，是否把六次
  START 视为一个动作是产品/行为决定，不在本单；本次只让之后的正式路径表达正确。
* **同时给 bodyact 增加"重复同姿态也算新动作"**：否决。owner 的 `already_in_pose` 门、
  机会源排除当前姿态、LR-5 单事实匹配都是既有边界；本轮不设计多步 BODY。

## Consequences

* 同一已提交 Decision 的正式重试/丢答对账稳定落在同一个 step/Action（只增加 attempt），
  不同 Decision 在同 place/revision/target 下得到各自的 step/Action 与
  activity_ref/decision_ref/authorization_ref。
* 无 Decision 的手工步骤仍按旧 key 工作，已有调用与既有测试不被破坏；代价是这条 fallback
  不具备正式身份保证，需要在后续调用方里继续传 Decision。
* 离线验收见上节“验收分层与父级独立复跑”：35 例与 smoke 两部分分别覆盖正式 gate-off 身份
  独立、以及隔离 AR-0 下的 UNKNOWN 对账与“成功回执不跨新 Decision”。
* 未证明：自然模型采用、G1、24h；真实 World 不会自然连续执行两次相同 POSE（owner no-op
  保留），所以"成功层"的身份用隔离 AR-0 + 真实 owner 验证，开闸后的完整归属效果不在本单。
* 相关笔记：[BODY 主体姿态与休息](../feature/2026-10-26-v04-day-life-body-actions.md)、
  [BODY 单动作 readiness](2026-10-25-body-single-action-readiness.md)、
  [v04 一天生活与世界日常动作](../architecture/2026-11-13-v04-lived-day-world-daily-actions.md)。
