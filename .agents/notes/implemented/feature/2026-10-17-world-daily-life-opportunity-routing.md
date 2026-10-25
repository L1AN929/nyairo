# Agent Note: 世界日常的机会翻译按 target 命名空间分流（地点 = 旅程，物件 = 消费）

Status: implemented

## Problem

W 包给了只读机会目录（`location_opportunity_catalog`），B6-5 的翻译层 `world_move_opportunity_source` 只懂一个 verb：它从 selector 可见的 action space 里逐个 `MOVE` 候选翻成 `target_refs: ["place:<id>"]` 的观察机会。两个后果：

1. **消费永远进不了候选**：F 的 `CONSUME` 候选在 gateway 的 `get_available_actions` 里（owner 自己算出的 live placement + 声明 + 营业 + 姿态），但翻译层从不读那一块，所以"她看见一份能吃的东西"这件事在 AG-0 里根本不存在；
2. **就算手工塞进去也会走错 owner**：`AutonomousLifeCycle._candidate_is_world` 用 `place:` 前缀判定"这条候选归 World"，`_world_step_spec` 也只从 `place:` 取目的地。一个以物件为目标的候选会被判成"不是世界动作"，落到作品/阅读分支上——用一次 Supply 提交去假装吃过，或者干脆报"没名字的世界目标"。

同时，机会条目本身缺"来源/不确定/距离"这类事实：AG-0 的 `ObservedWorldOpportunity` 只接受它声明的字段（多一个键就 `TypeError`），所以丰富事实不能塞进候选合同，必须有第二个、明确非授权的视图。

## Decision

翻译层（`components/life/scripts/world_move_opportunity_source.py`）改成按**两种 target 命名空间**产出，并在每个机会上保留它自己的来源：

- `observed_move_opportunities` 不变（`place:<destination>`，来自 selector 可见 action space）：**机会集与可选集是同一集合**这条性质原样保留。
- 新增 `observed_consume_opportunities`：只读 gateway 自己的 `get_available_actions` 的 `CONSUME` 条目；`available is not True` 就一个都不产出（"没暴露"不是"也许可用"），每个候选翻成 `object:<object_id>`。
- 新增 `opportunity_view`：与 AG-0 记录并行的**非授权**视图，带 `kind`/`verb`/`source`/`availability`/`availability_reason`/`walk_seconds`/`place_observed`/`valid_from`/`valid_until`，全部 `action_grant: false`。地点事实来自 W 目录（`read_location_catalog`，只读、失败即"这一轮没有地点事实"，绝不补一个"可能开着"）；目录的 `now` 必须是 aware `datetime`（它的 `_instant` 拒字符串），所以 provider 统一用 `_moment()` 交对象、用 ISO 只做机会 id 与有效期。
- 周期侧（`autonomous_life_cycle`）把 `place:` 与 `object:` 都算作"世界候选"（`_candidate_is_world`），`_world_step_spec` 按命名空间分流：`place:` → `MOVE`（计时旅程），`object:` → `CONSUME`（瞬时 owner 变更，`idempotency_key` 里带地点、世界修订号与 instance）。idempotency key 带 instance 是刻意的：重试必须复用同一次执行，而不是吃掉第二份。
- 采纳形状也分开：`GO_TO_OBSERVED_PLACE`（`WORLD_ACTION_BOUNDED` + `MAX_CONFIRMED_POSITION`，完成判据是"到达 Decision 选的那个地点"）与 `CONSUME_OBSERVED_OBJECT`（`WORLD_ACTION_BOUNDED` + `TARGET_STATE`，完成判据是 owner 回执里的 `object_id` 对应的 `world_fact:object:<id>:consumed`）。`target_refs` 仍由机会集整体生成——周期不自选，选择是 AG-1 的事。

## Alternatives considered

- **把距离/营业事实塞进 `ObservedWorldOpportunity`** — 一处给全，接线最短。否决：那是 AG-0 的冻结记录合同，多一个键就构造失败；而且"候选记录里带授权式事实"正是该合同防的东西。改成并行视图，候选合同一个字没动。
- **让翻译层自己算可达地点（用 W 目录的 route/秒数产出多跳 MOVE 机会）** — 机会会更丰富（从家能看到公园/咖啡店/阅读角）。否决（本版）：机会集必须等于"World 此刻真的允许她选的那一组"，而 gateway 的 MOVE 候选是当前地点的单跳邻居；目录里的多跳是**路线事实**，不是 owner 给出的可选动作。多跳要落地得先让 owner 的 MOVE 候选放宽，否则就是在 Life 侧造第二份可选集。目录事实照读，只用于报告与不确定性。
- **把 `object:` 也当 `place:` 交给 MOVE** — 不改周期侧任何代码。否决：那会让一次消费变成一次移动，位置、旅程账本与完成判据全错，正是"把所有世界动作按 MOVE 的完成证据处理"这个反例。
- **用 `latest_*` 或"哪个候选先出现"决定走哪条 owner** — 少写一个前缀判断。否决：那等于让候选顺序变成权限，与"选择归 AG-1、路由归已提交 Decision 自己的候选记录"冲突。
- **把地点事实的 `now` 直接传 ISO 字符串** — 与机会 id 用同一个字符串，少一个转换。否决（实测过）：W 目录的 `_instant` 拒字符串，整次目录读会失败并被如实报成 `CATALOG_READ_FAILED`，于是"距离/营业"永远为 `NOT_READ`。

## Consequences

- **收益**：一次观察能同时给出两类合法机会（一个她真能走到的地方 + 一份 owner 说此刻可用的物件），每条都带 subject / 来源 / 时间窗 / 有效期 / 不确定性与距离；候选合同不变，丰富事实在并行视图里，谁也不能把它当授权。
- **收益**：消费与移动各自走对的 owner 与各自的完成判据——消费靠 owner 回执，移动靠计时到达；"到达"再也不会被一次消费冒充，"物件不见了"也不会被当成吃过。
- **代价与已知上限**：`MOVE` 机会仍是 owner 的单跳候选（本笔记 Alternatives 里说明为什么本版不扩）；`walk_seconds` 对 MOVE 条目来自目录的路线求和，因此是"从当前位置走过去要多久"，不是单跳时长。
- **代价与已知上限**：目录读失败只降级为"没有地点事实"（`availability` 为 `NOT_READ`），机会本身照旧产出——这是刻意的：营业事实缺失不该让"能走到的地方"消失，但任何消费候选仍由 owner 自己判（`available=False` 就不产出）。
- **后续（2026-10-22）**：`_candidate_is_world` 的五类 target 前缀语义不变；只有 World 一条路径可走时，分派点在进入 World builder 之前也复用同一谓词做预检，见
  [2026-10-22-single-world-candidate-guard.md](../bug-fix/2026-10-22-single-world-candidate-guard.md)。
  该篇只补“拒绝前不构建 World 形状”的防误送缺口，不改本笔记的命名空间分流决定。
- **后续（2026-10-23）**：本笔记的机会有效期窗口必须与本回合的 `observed_at` 同域。
  压缩演示时钟下 provider 曾按真实墙钟盖章，而 cycle 用业务时钟校验，机会被整批读成
  `NOT_YET_OPEN`/`EXPIRED`。修复只在既有接缝把回合时刻传给
  `observe(occurred_at=...)`，并在压缩模式下把业务时钟交给 provider 的
  `now`/`observed_now`；`place:`/`object:`/`stay:`/`bodyact:` 的产出与 AG-0 资格政策
  一字未改，见
  [2026-10-23-explicit-compressed-demo-clock.md](../architecture/2026-10-23-explicit-compressed-demo-clock.md)
  的收口条目。
