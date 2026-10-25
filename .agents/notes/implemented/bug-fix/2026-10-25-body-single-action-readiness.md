# Agent Note: BODY Activity 只有一个动作，就如实撤回 CONTINUE

Status: implemented

## Problem

fastday-k（`run:fastday-k`，2026-10-24 22:55–23:25 CST）的自然链走到了
`START 阅读 → PAUSE → START 身体动作 → CONTINUE` 之后卡住：

* 身体动作在正式链上被 `WORLD_BODY_INTENT_MUTATION_GATE=off` 挡在预提交之前，
  已交付的分类如实报了 `STEP_REFUSED`（Action `PREPARED`、attempt
  `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执）；Activity 仍是 `ACTIVE` 并占着前台。
* 下一轮 AG-1 的候选面仍然提供 `CONTINUE`（`life_step_readiness` 对任何已接 World
  adapter 的目标一律答"有步"），但执行端按既有规则不构建 step，CONTINUE 只能落到
  `NO_STEP_AVAILABLE`。候选说"能做"、执行端说"没步"，两面不一致。

根因不是 `has_foreground` 少改一行：一个 `bodyact:` target 就是**一次**动作
（`life_body_action_targets.build_body_action_step` 只产出一份 payload，幂等键绑定
place + revision + target，完成判据是单一 `WORLD_FACT_MATCH`；`REST` 的 start/stop
是两个独立编码，不是同一 Activity 的两阶段）。整棵树没有为 BODY Activity 定义过
第二个步骤：没有同 Action id 重试政策，也没有"重复同一姿态算新进度"。阅读与 stay
各有前台特例，BODY 没有，也不需要——它本来就没有下一步。

## Decision

1. **readiness 如实回答**：`life_step_readiness.assess_step_readiness` 在
   Activity 是 `BODY_ACTION_AT_OBSERVED_PLACE`（或目标就是 `bodyact:` 命名空间）且
   已绑定 activity/target/ACTIVE 时，返回 `available=False`，原因
   `BODY_SINGLE_ACTION_NO_FURTHER_STEP`。未绑定、非 ACTIVE、未知命名空间、caller
   不能行动仍答 `None`（不知道），不会被这条规则借成"没有下一步"。
2. **沿用既有投影，不新增通道**：该答案经 cycle 既有的
   `current_step_unavailable_true` 证据键进入 AG-1。AG-1 既有规则只撤当前
   `CONTINUE`，`PAUSE`/`ABANDON`/`DEFER` 与 park 提供的 `WAIT` 按原安全门保留。
3. **不碰 Action 结算**：预提交拒绝仍是 `PREPARED` + `PRE_SUBMIT_FAILED`，不改终态、
   不重发、不新增重试；`ACTIVE` 也不再被解释成"动作一定没成功"（提交、结算、
   LR-5 完成是三层证据）。
4. **不动其它契约**：AG-1/AG-2/AR-0/LR-2/LR-4、World step builder、`world_fact:`
   编码、Body/owner 接口、落盘格式、默认 gate 与预算都不变；该原因也不进入
   `life_wait_basis` 的命名依赖，不会凭空生成等待。

## 为什么改候选面而不是补"再接一步"

BODY 的 `CONTINUE` 若要真有步，只有两条路：同一 Action id 重投，或把重复动作当新
进度。前者是新的重试合同（谁触发、上限、退避、与 `open_submission_intents` 的关系，
以及 gate off 时每轮必再拒并消耗请求额度），后者与机会源"排除当前姿态"、
owner 的 `already_in_pose`、LR-5 单事实匹配直接冲突。两条都要先定义新语义；本轮
用户裁定的是让候选面诚实，而不是给 BODY 造第二个动作。

## Alternatives considered

* **同 Action id 重试政策**：最强理由是"预提交明确没发，重试不会产生重复效果，
  gate 恢复后还能做完同一件事"。否决：触发方、次数上限、退避、与未决提交账本的
  关系全是新合同；gate off 期间每轮重试都会被同一道门再拒，白耗预算。
* **把重复同一身体动作当新进度**：最强理由是能保住"继续"这个直觉用法。否决：
  与机会源排除当前姿态、owner 自己的 `already_in_pose`、LR-5 单事实完成判据冲突，
  等于重新定义"进度"。
* **解除 `has_foreground` 或把 `world_fact:` 包装解析成 body step**：最强理由是
  改动最小、能直接让 CONTINUE 拿到 step。否决：那只是绕开路由过滤，实际执行仍是
  重投同一动作，属于上面两条被否决的方向；还要改 World step builder/编码读法。
* **删掉整个 CURRENT_ACTIVITY 候选，或直接强制 PAUSE/完成**：最强理由是能立刻
  消除"候选与执行不一致"。否决：会把 PAUSE/ABANDON/NO_ACTION 一起撤掉并替她做
  退出，扩大安全门；本条的边界只是"这个 Activity 没有下一步"。

## Consequences

* 候选面与真实执行能力一致：同一个 BODY Activity 不会再出现"CONTINUE 合法、
  执行却 `NO_STEP_AVAILABLE`"的自相矛盾；k 那类状态现在可以选 `PAUSE` 等既有出口。
* 代价：一次被预提交拒绝的 BODY Activity 不会自己重试；即使 gate 恢复，也要等新的
  机会/Decision 开新 Activity，同一 Activity 不会自己续上。它不是自动完成或自动
  放弃——Activity 仍由 owner 保持原状态，直到 Decision 选择退出。
* `ACTIVE` 的含义被限定回"Activity 尚未关闭/完成判据未被确认"，不再反推动作是否
  成功。这条纠正只影响说明与判定，不改任何结算路径。
* 多步身体活动不在本条内；要做必须另开设计（新的动作/进度语义）。
* 验证：`tests/test_body_single_action_readiness.py` 与
  `tests/test_day_life_decision_legality.py` 27 例；`scripts/body_single_action_readiness_smoke.py`
  走正式 `evaluate_once` 两轮（gate off 的明确拒绝 → 真实模型请求里无 CONTINUE、
  有 PAUSE → 正式 AG-2 采纳并释放前台），返 0。未证明真实模型自然采用、不是 G1、
  不是 24h，也没有产品模型调用。

关联：[BODY 动作机制与 2026-10-25 STOP 历史](../feature/2026-10-26-v04-day-life-body-actions.md)。
