# Agent Note: 0.7 PLACE 台面真相对齐、明确拒绝的单步收尾与有界实验首故障闸门

Status: implemented

## Problem

0.7 唯一真实短日 A（`scripts/v07_live_a.py`，2026-11-08 21:05–21:35 CST）里，
home 里 `PICK_UP home_notes07` 与阅读都成功了，随后 `Decision` 选中
`dailyact:place:surface:home:bed_surface:home_notes07`，World owner 却回
`REJECTED` / Action `FAILED` / 回合 `STEP_REFUSED`；下一轮 `CONTINUE` 又只得到
`NO_STEP_AVAILABLE`，`actv_35236203f1224404` 仍 `ACTIVE` 且占着前台。三个缺口叠在一起：

1. **台面候选与 Resolver 用了两套真相。** Life 候选里的台面来自 owner 自己的
   `get_environment -> surfaces_here`；`REST` 也用同一份投影判断“床真的在这里”。
   但 `world_action_resolver._m15_destination_verdict` 对裸 `bed_surface` 额外要求
   经济 owner 里有一个 `OWNED` 的 `basic_bed`。默认新生成世界（`_prepare_world`）把
   worldgen 的床对象放在 `bed_surface` 上，经济 genesis 的 `items` 是空的——于是
   owner 对 Life 说“台面在”，提交时又用更严的第二套规则拒绝。
2. **明确失败没有收尾。** `_classify_world_step` 把终态 `FAILED` 只报成
   `STEP_REFUSED`；Activity 仍 `ACTIVE`，dailyact 的单步 ready 也仍说“有步”，
   下一轮 `CONTINUE` 合法、执行端却无步。
3. **有界实验只看 provider 故障。** `bounded_failstop` 的 marker 只由请求预算的
   provider/transport 失败写入；owner 明确拒绝这类真实执行失败没有同步闸门，运行继续
   烧到窗口结束。

## Decision

**一、裸 legacy surface 的合法性改用 owner 自己的实时投影。**
`_m15_destination_verdict` 的 `surface` 分支在 `surface_declared` 之后，对“有
owner catalog、但没有经济 OWNED 实例”的裸 surface（`bed_surface`）改为读
`substrate.surfaces_here(live, owned_catalog_map, location_id=...)`：这份投影就是
`get_environment` 返回、`REST` 也在用的同一份事实。台面真的在这里就允许 PLACE；
不在这里仍按原规则拒绝。实例表面（`desk_surface:<item>`）仍要求经济实例 +
同地点 live placement，两张书桌依旧不能共享一个全局 id；未声明的 id 仍被
`surface_declared` 拒绝。这是把 Resolver 对齐到唯一一份 surface 真相，不是放宽成
“任意台面都能放”：非法台面不在投影里，仍然执行拒绝。

**二、单步 World 动作明确失败后由 owner 的正式转场收尾并释放前台。**
`_classify_world_step` 只在 Action status 是终态 `FAILED`（owner 明确拒绝/执行失败、
无未知副作用）时调用 `_close_refused_single_step`：从该 Action 的 owner 回执取一个
合法 basis ref，调用既有 `ActivityOutcomeService.block_activity`（LR-2 `BLOCK`，
`BLOCKED_BY_CONDITION`，`blocked_reason_code=ACTION_TERMINALLY_REFUSED`）。失败
Action 与回执原样保留，Activity 不写成 `COMPLETED`，前台槽释放，下一轮可以开始别的
行动；没有重发、没有替她选替代动作。`UNKNOWN` / `IN_TRANSIT` / nochange /
`NO_ACTION` 不进这条路径，`UNKNOWN` 仍按原 execution 对账。dailyact 在
`life_step_readiness` 里和 BODY 一样回答“没有下一步”，所以即使收尾被跳过，
`CONTINUE` 也不会再合法地落到 `NO_STEP_AVAILABLE`。

**三、有界实验的 first-fault 闸门覆盖明确 owner 拒绝。**
带 `world_step_failure` 的回合在 `_finish` 里立即调用请求预算的
`note_failure("STEP_REFUSED")`，写的是与 provider 失败相同的四键 marker（固定
category `STEP_REFUSED`，不存异常正文）。下一次 `reserve`/`send` 前
`LifeRequestBudget.check_failstop` 就会拒绝，supervisor 下一轮也据此 stop-close。
日常预算 `failstop=False` 时同一个调用什么都不写，所以不会给日常实例留永久锁。

## Alternatives considered

- **把 `surfaces_here` 收紧到 Resolver 的经济归属规则（只报告会被接受的台面）。**
  最强理由：候选面与执行面立刻一致，最省事。否决：默认新世界的床是 worldgen
  fixture、不是经济实例，收紧后默认世界一块台面都没有，等于用“删掉所有 surface、
  只留 room”逃验收；而 owner 自己已经用同一份投影做 REST 与 `get_environment`，
  收紧会把“床在这里”也一起变成假。
- **给 Resolver 加一个“实例表面只需 substrate 里的 owner 对象”规则。**
  最强理由：worldgen 床本来就在 substrate 里。否决：那会让没有经济归属的对象
  也拥有台面，撤掉“两张书桌不能共享一个全局 surface”的来源约束，属于放宽 owner
  校验。
- **失败时同步写终态 `FAILED`。** 最强理由：语义最像“这件事失败了”。否决：S09 的
  `fail_activity` 要求 owner 失败评估（`ARTIFACT_ARCHIVED`/`TOMBSTONED`/
  `SOURCE_WITHDRAWN`/`EXTERNAL_TERMINAL_REFUSAL`），而它按设计拒绝把“一次失败的
  Action”当成“目标不可达”；单次 `REJECTED` 的 Action 行也没有 `terminal_refusal`
  事实。要让它成立得新增生命周期权限/owner 事实，本单按停止条件不绕过。
- **失败时同步写终态 `ABANDONED`。** 最强理由：ABANDON 是现成的终态，且不冒充完成。
  否决：LR-2 的 AGENCY_AUTHORIZED permit 要求本次 Decision+Adoption 带同一个
  `authorized_command_id` + `ABANDON` 授权，本轮的授权是 START/CONTINUE 的；直接
  调用会得到 `AUTHORIZED_COMMAND_SCOPE_MISMATCH`。造一个 Decision/Adoption 或新增
  ABANDON consequence capability 都是新权限/owner 合同，同样按停止条件不绕过。
  因此本单用既有 `BLOCK`（有 consequence capability、有 named basis、真的释放前台、
  保留失败证据），终态 ABANDONED/FAILED 留给下一轮真实 Decision 或后续授权单。
- **只改 readiness，不碰 Activity。** 最强理由：不写任何状态。否决：Activity 仍占
  前台，下一轮没有可执行步，用户可见的“卡住”还在。
- **把每次 `STEP_REFUSED` 都当实验失败。** 最强理由：实现最简单。否决：阅读响应失败、
  内容写入失败等也会报 `STEP_REFUSED`，它们不是 owner 对 World 动作的明确拒绝；
  只在回合真的带 `world_step_failure` 时落 marker。

## Consequences

- 默认新世界的 `PICK_UP -> PLACE surface` 现在能走通：`get_environment` 列出
  `bed_surface`，Decision 选中 `dailyact:place:surface:home:bed_surface:<object>`，
  Resolver 接受，substrate 真的写成 `{kind: surface, surface_id: bed_surface}`，
  Activity 由 LR-5 正常 `COMPLETED`，前台释放。`scripts/v07_daily_life_demo.py`
  改为优先同 `bed_surface`，并断言这三件事。
- 明确拒绝的单步 World 动作会得到 owner 的 `BLOCKED` 收尾（失败 Action/回执保留、
  不 `COMPLETED`、前台释放），下一轮能选新行动；`UNKNOWN`/在途/空回合不受影响。
- 有界实验在首个明确 owner 拒绝后、下一次模型预留前停止；日常预算不受影响。
- **边界**：同步的终态 `FAILED`/`ABANDONED` 需要新的生命周期权限或 owner 事实
  （S09 的 `terminal_refusal` producer，或 ABANDON consequence capability），本单
  按“遇新权限/owner 合同先停”的规矩没有造它；已交付的是既有 `BLOCK` 收尾。
  旧 live-a 根、旧记录、历史账本未迁移、未解冻、未重放。
- 验收：`tests.test_v07_daily_life` 26 例（含一条真实 owner 拒绝 → 完整 Activity
  处理链，不是手造终态字典）、`scripts/v07_daily_life_demo.py`、`scripts/v07_live_a.py
  offline-check` 三命令 exit 0。相关既有 `tests.test_action_preparation_behavior` 20
  例与 `tests.test_world_daily_hands` 12 例仍 OK。
- 相关既有决定仍有效：
  [[2026-11-08-daily-life-continuity]]、[[2026-11-08-v07-live-a-thin-entry]]、
  [[2026-11-06-world-move-no-further-step]]、
  [[2026-11-13-v04-lived-day-world-daily-actions]]。
