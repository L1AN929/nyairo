# Agent Note: World 移动 Activity 的唯一 Life 步骤是出发，到达不是又一步

Status: implemented

## Problem

idle2 唯一真实批（`run:fastday-idle2-20261106`，2026-11-06 10:21:49–12:14:01 CST）
里，同一个 `GO_TO_OBSERVED_PLACE` Activity 一直占着前台。
12 轮正式 Decision 全部是 `CONTINUE`（每轮一个不同 `dec:`，都选同一个
`cand:50a1bfa83a78a2aa`），而执行端 12 次都报 `NO_STEP_AVAILABLE`，detail 是
“adopted, but no executable step exists in this revision”。

同一批的只读投影已经给出两面不一致的直接证据：

* `step_readiness` 每轮都是 `available=True`、`reason_code=WORLD_STEP_AVAILABLE`、
  `target_ref=place:street_1`（`autonomous_wakes.jsonl` 的 `extra.step_readiness`）；
* 执行端在 `has_foreground=True` 时根本不构建 `world_step`
  （`autonomous_life_cycle.py:6489`，条件是 `act and not has_foreground ...`），
  同一轮又没有任何其它 step spec，于是落到最后的
  “adopted, but no executable step exists in this revision”。

这不是“无失败锁”或“没有模型决定”：真实 AG-1 给了 `START2/PAUSE1/CONTINUE12`，
失败锁是后来 `MODEL_CONNECTION` 才置上的。也不是 `has_foreground` 写错了一行：
一个 MOVE Activity 的 Life 步骤只有**出发提交**这一次。出发之后：

* 分段推进由旅程引擎自己在 `advance_journey` 的 `_step` 循环里完成，每段各铸一张
  一次性授权，不需要新的 Decision；
* 到达结算由 `settle_world_journey` 读 World owner 自己的观察、再交 LR-5 完成判定，
  它不是一步可执行的 Life 步骤。

所以“候选面说能做、执行端说没步”是与 BODY 单动作同一形状的缺口
（见 [BODY 单动作](../bug-fix/2026-10-25-body-single-action-readiness.md)），只是机制
不同：BODY 是同一动作没有重试/第二阶段，MOVE 是唯一一步已经发出、后续属于 owner。

本批还暴露了一个**不同的** World 层缺陷，本次不修：journey 的 `blocked` 记录是
`ARRIVAL_REALIZATION_FAILED`（00:18:34Z），但同刻 `world_travel_delivery.json` 里该段
已是 `ARRIVED`、`world_state.json` 位置也已在 `street_1`。这是 `realize_due_travel`
在已写盘后报失败的故障窗口，按引擎自己的故障窗口 ③ 本应“补写账本”，但 `blocked` 是
终态，`advance_journey` 不再推进。它属于旅程/旅行投递层，不在本单写集。

## Decision

1. **readiness 如实回答**：`life_step_readiness.assess_step_readiness` 在 Activity 是
   `GO_TO_OBSERVED_PLACE`（或 target 落在本树唯一承载移动的 `place:` 命名空间）且已绑定
   activity/target/ACTIVE 时，返回 `available=False`，原因
   `WORLD_MOVE_NO_FURTHER_STEP`。未绑定、非 ACTIVE、未知命名空间、caller 不能行动仍答
   `None`；stay 分支先于它，BODY 分支也先于它，二者行为不变。
2. **沿用既有通道**：该答案照旧经 `StepReadiness.fact_keys()` 产出
   `current_step_unavailable_true: True`，AG-1 既有规则只撤当前 `CONTINUE`，
   `PAUSE`/`ABANDON`/`DEFER` 与既有 WAIT 依据全部保留；它也不进入
   `life_wait_basis` 的任何依赖来源。
3. **不动执行端与 owner**：不构建第二段 Life step、不改 `has_foreground`、不重投 MOVE、
   不新增重试政策；到达结算、LR-5 完成判定、Activity 终态和 World 旅程账本都不改。
4. **不动默认策略与候选保底**：没有给 GO_TO 加“自动完成/自动放弃/转发呆”，也没有删掉
   CURRENT_ACTIVITY 候选。

## Alternatives considered

* **在 `has_foreground` 时也构建 `world_step`，让 CONTINUE 真的再发一次 MOVE**：最强
  理由是“继续”直觉上就该推进旅程。否决：同一 Activity 再发一次 MOVE 是新的重试合同
  （触发方、上限、退避、与未决提交账本/一次性授权的关系），而旅程引擎本来就会在段确认
  后自己发起下一段；补一条会制造重复提交和双份授权，且 gate/失败时每轮白耗请求。
* **让 `settle_world_journey` 在 `blocked` 时直接用 World 位置完成 Activity**：最强理由
  是引擎故障窗口 ③ 写得很清楚（“travel 记录 ARRIVED 且位置在段终点 → 只补写账本”），
  而本批证据恰好是旅行记录 ARRIVED、位置在终点。否决：`blocked` 是引擎明确规定的终态，
  自动解冻它等于改旅程状态机；那属于 `chiyo_bundle/world_journey.py` /
  `world_travel_delivery.py` 的故障恢复合同，要先定“部分写盘失败后谁补账”，不能借
  Life readiness 一单顺手改。活跃旅程的正常到达（`completed` + 位置一致）仍由原路径结算。
* **改 `has_foreground` 或删掉整个 CURRENT_ACTIVITY 候选**：最强理由是改动最小、立即
  消除不一致。否决：会同时藏掉 reading/stay 等真正还有步的路径，或把 PAUSE/ABANDON
  一起撤掉并替她做退出，扩大安全门。

## Consequences

* 在途或已出发的 GO_TO Activity 不会再出现“CONTINUE 合法、执行却 `NO_STEP_AVAILABLE`”；
  候选面保留 PAUSE/ABANDON 等既有出口，选不选仍由 Decision 决定。
* 代价：本单只让候选面诚实，**没有**修好上面那个 World 层 `ARRIVAL_REALIZATION_FAILED`
  故障；一次已经 `blocked` 的旅程仍会占着前台直到她自己选择退出。要修它必须另开一单，
  先定旅程终态与旅行投递的部分写盘恢复合同。
* 未证：不覆盖真实模型自然采用（本单用脚本 Decision 走正式 AG-1/AG-2）；不覆盖多段
  长旅程的每一步；不覆盖 blocked 旅程的恢复。验证只用隔离 scratch 中的真实
  ProductionLifeRuntime + 真实 Supply/World owner + 正式 gateway，网络 AF_INET/INET6/DNS
  硬拒、AF_UNIX 放行。
* 验证：`tests/test_idle2_no_step.py` 7 例（6 条本缺口定向断言 + 1 条正式短链），
  `scripts/idle2_no_step_check.py offline-check` 返 0；相邻的
  `test_body_single_action_readiness`、`test_life_wait_resume_v04`、
  `test_day_life_decision_legality`、`test_single_world_candidate_guard`、
  `test_one_day_life` 全绿。

关联：[idle2 观察批](../testing/2026-11-06-idle2-bounded-observation.md)、
[World 旅程逐段授权](../bug-fix/2026-10-23-world-journey-segment-authorization.md)。

后续（不改写上面的旧判断）：正常到达接缝已在
[World 到达已落盘时只补写账本](2026-11-06-world-arrival-realization-boundary.md)
修复；已 `blocked` 旅程的恢复仍未做、也未获授权。
