# Agent Note: 停留可以按选择提前结束；街区多地点连续链沿用同一套 owner

Status: implemented

## Problem

上一批证明了"她能在某个地方待一会儿，并且只有世界仍把她放在那儿、声明时长真的过了才算完"。
但它只覆盖了**一种**结局：等到点。真实生活里还要能"坐了一会儿就起来走"——而这件事在代码里
根本不存在，且很容易做成假的：

1. **提前结束没有产品通路。** 停留的 Activity 带着一个"声明时长到了"的完成判据，前台由它占着；
   想离开只有两条歪路：把 elapsed 改大（伪造已经待满），或者干脆伪造那条 `world_stay_ref` 完成
   事实。两种都会让"她待了 300 秒"变成模型或测试说出来的话。
2. **没人检查"现在还能不能待"。** 停留的机会只在 owner 说 OPEN 时产出，但**继续**一轮的
   checkpoint 从不复读这条事实：店 18:00 关门之后，下一轮仍会记录一次"她还在店里坐着"，甚至
   （如果时间也过了）写出完成事实。
3. **重开会把时钟重置。** 停留的时长是"LR-5 记下的第一个 checkpoint → 这一轮 observed_at"。
   一个被暂停/恢复的停留如果又走一次 START，起算点就被悄悄挪到新的瞬间，声明时长可以被"待两次"。
4. **单地点。** 家—门前街—家的短程证明不了"街区里的连续生活"：没有一次多地点、正时长、
   带回程的连续链，也没有在途中"因为店主关门而改主意"的实证。

## Decision

### 提前结束是一条真实的 Decision 通路，不是一次改写

`autonomous_life_cycle.evaluate_once` 在前台是一个停留 Activity、且已提交的 Decision 是
`PAUSE` 或 `ABANDON` 时，交给 `_close_stay_by_choice()`。这条路径只做三件事，而且三件都是读：

- **它真的结束了吗**：从 Activity owner 自己再读一次那个 Activity。仍是 `ACTIVE` → 报
  `STEP_REFUSED`（`OWNER_DID_NOT_APPLY_THE_EXIT`）并明说"前台没变"，不宣布"她离开了"。
  Adoption 被拒（`REJECTED*`）→ 报 `OUTCOME_ADOPTION_REJECTED`，同样不带 exit 事实。
- **它到底持续了多久**：`_stay_began_at()` 读 LR-5 记下的**第一个** checkpoint，与这一轮显式
  `observed_at` 相减；`remaining_seconds` 如实写成剩余。
- **它没变成什么**：`completed: false`、`completion_claimed: false`，并且**不调用**
  `_record_world_stay_progress()`——所以 `WORLD_STAY_FACT_KEY`（`world_stay_ref`）那条完成值
  在这个 Activity 上一辈子不会出现，提前结束永远不可能满足"待满"判据。

报告字段（`base["stay"]`，`step_path = "STAY_ENDED_BY_CHOICE"`）：`phase`（`ENDED_EARLY` /
`PAUSED`）、`exit_decision`/`exit_kind`（`ABANDON`/`PAUSE`）、`started_at`（owner 的 checkpoint）、
`observed_at`、`elapsed_seconds`、`remaining_seconds`、`owner_status`、`owner_ended_at`、
`owner_terminal_reason_code`（LR-2 写的是 `ABANDONED_BY_DECISION`）、`owner_last_transition_ref`、
`foreground_released`。`outcome` 沿用既有词汇（`ABANDONED` / `PAUSED`），不新增状态机。

**单前台**：`ABANDON` 由 AG-2 既有的 LR-2 状态命令执行（`activity_command.abandon_activity`），
Activity 变成 `ABANDONED` 并释放前台，于是**下一段 MOVE 合法**；这里不写第二条前台路径，也不碰
AG-1/AG-2 的合法矩阵。

### 提前离开的停留不被当作"可以回来续"的停留

`resumable_as_stay: false` 是事实而不是建议：时长从第一次 checkpoint 起算，暂停期间她人在别处，
把那段算成"在场"就是 P14 明令禁止的伪造（"没有真实可继续步骤，长 timer 不能冒充画了一下午"）。
因此本批次只支持 `ABANDON` / 明确结束这一条提前退出；`PAUSE` 会准确报成"已暂停且不会被当作停留
恢复"。为堵住"恢复后再 START 重置时钟"，`_classify_stay_step()` 对**已经有第一个 checkpoint** 的
`START` 直接拒绝（`STAY_ALREADY_STARTED`）。

### 场所关着就不许再造"她坐在那儿"

`_classify_stay_step()` 在写任何 checkpoint 之前，用 `_place_availability()` 复读**同一次观察**
读到的营业事实（`_observe_position()` 现在把 provider 的 `last_catalog` 一并刷新进
`self.last_world_catalog`，一个缝、一个时刻）。owner 说 `CLOSED` → `STEP_REFUSED`
（`WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT`），不写 checkpoint、不写完成、不释放前台；读数不可用
（`None`）不会被升级成 OPEN 也不会被降级成 CLOSED。于是"关门之后的继续"与"到点完成"是两件必须
分开的事：时间过去 ≠ 待满了。真正可行的出口仍是下一轮那个合法的 `ABANDON`，它的记录照样写着
`completed: false`。

### 地点事实的判定沿用同一个 seam

`_place_availability()` 只读 `self.last_world_catalog`（W 目录，`world_daily_opportunities` 的
投影），不新开 store、不问模型、不加第二个时钟。它返回的就是 owner 拼的那个状态字符串。

## Alternatives considered

- **让提前结束也写一条完成事实（值改成"提前结束"）** — 表面上让 Activity 能正常收尾、LR-5 一句话
  就完成。否决：完成判据是"声明时长到了"，把"没到"写成完成就是把判据本身废掉；下次任何"到点"
  的证据都不再有意义。
- **把 elapsed 改成声明值来"合法地完成"** — 改动最小、下游一行不用碰。否决：这是直接编造"她在那
  儿待了 300 秒"，也是本批要禁止的第一件事；owner 的 checkpoint 与 observed_at 都还在盘上，
  一眼就能对出来。
- **给 Life 加一个"停留提前结束"的新命令/新 owner** — 概念最干净。否决：AG-2 已经有
  `ABANDON_CURRENT_ACTIVITY`（LR-2 `abandon_activity`，`reason_code=ABANDONED_BY_DECISION`），
  再造一个并行状态机就是第二个真相，还要重做单前台与 handoff。
- **实现完整的 PAUSE→RESUME 计时系统（暂停期间不计入时长）** — 语义最完整。否决（本批）：那需要
  owner 侧"暂停区间"的真实记录与恢复时的重算规则；在没有它之前，把暂停过的停留当成可恢复，必然
  把"离开的那段"算成在场。代价写进 Consequences。
- **关店之后仍然写完这一轮 checkpoint（"她坐在关着门的店里"）** — 只多写一行、下游全绿。否决：
  这正是"关闭/位置条件变化不能继续制造 SIT/READ_HERE 已发生"。
- **把多地点路线写进 Life** — 报错最好看、断言最好写。否决：产品一旦持有"远处可选地图"或固定
  route，就不再是"从 owner 此刻的单跳候选里选"；本批的路线只活在确定性替身自己的出门计划里。
- **为多地点再造一套行程 owner** — 否决：既有 Activity 的 `subject_refs` / LR-5 checkpoint 已经
  是真相，第二份行程账本重启后必然漂移。

## Consequences

- **收益**：街区里第一次有"坐了一会儿就起来走"的可验证通路：`ABANDON` → LR-2 状态命令 →
  `status=ABANDONED` / `ended_at` / `terminal_reason_code=ABANDONED_BY_DECISION`，Life 侧只写真实
  起止与 `completed: false`，前台真的空出来，下一段 MOVE 合法。
- **收益**：`closed` 与 `completed` 现在是两个独立事实：店关门那一轮被安全边界拒绝，既不写
  "她还坐着"，也不假装待满。
- **收益**：同一实例在一次 episode 里走了 10 条真实单跳边（90/120/180/180/120/120/120/150/150/90 秒，
  全部正时长、全部来自 owner 的拓扑与 travel 记录），并且在同一个计划里因为"咖啡店 CLOSED"
  这一条 owner 事实而**不去**、因为同一事实变成 OPEN 而真的走过去。
- **代价与已知上限**：只有 `ABANDON`/明确结束这一条提前退出；`PAUSE` 会被准确报告，但**不能**被
  当作停留恢复（暂停区间不计时长这件事没有 owner 侧记录，本批不假装有）。
- **代价与已知上限**：`_place_availability()` 只认 W 目录里"这一刻"的营业状态；目录读不到时返回
  `None`，此时停留按原有规则继续（不会因为"读不出"就拒绝），这是刻意的失败开向——只有一个**明确
  的 CLOSED** 才会终止。
- **代价与已知上限**：替身的出门计划是 fixture 里的固定 hop 序列（这是"她的打算"，不是产品路由）；
  产品侧一行路线都没有，但也因此没有"等开门"的 due-time 机制。
- **代价与已知上限**：AG-2 在 ABANDON 上发的 handoff 没有 world refs，Memory 那句通用终止模板
  仍说"作品仍然留着"；本批不改 consumer（不在写集），如实记录。

## Verification

- `tests/test_world_neighborhood_life.py`（1，同实例连续 episode，`INJECTED_CLOCK` 单一时刻）：
  10 段真实单跳（出发≠到达、对账一次 UNKNOWN）、公园停留 `START → ONGOING → END`
  （`COMPLETION_ELIGIBLE`、前台释放）、街口 07:23 咖啡店 `CLOSED` 时 `NO_ACTION` 而 17:56 之后同一条
  规则真的走过去、咖啡店停留被"关门"这一事实提前结束（`ENDED_EARLY`、`completed:false`、
  `remaining_seconds>0`、`owner_terminal_reason_code=ABANDONED_BY_DECISION`）、回家 RESUME 原
  checkpoint 并 CONTINUE 出真实新版本、公园那次停留进真实 M0 且出现在下一次 `prior_experiences`。
- `tests/test_life_stay_early_exit.py`（2）：公园停留合法 `ABANDON`（`elapsed<planned`、
  `remaining` 如实、无 `checkpoint:stay:`、之后 MOVE 合法）；咖啡店里「不合法 ABANDON →
  `DECISION_FAILED`/`DECISION_CANDIDATE_INCOMPATIBLE`」「关门后坚持 CONTINUE →
  `STEP_REFUSED`/`WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT`、前台不释放」「随后合法 ABANDON →
  `ENDED_EARLY`、`elapsed>=planned` 但 `completed:false`」。
- 既有 `tests/test_world_daily_life_continuous.py` 与 `tests/test_world_daily_life_plan_change.py`
  原样全绿（旧停留行为未变），命令与真实退出码见 `docs/engineering/world-neighborhood-life-result.md`。
- 相关：停留机制本身见
  [2026-10-17-world-daily-continuous-stay-and-plan-change.md](./2026-10-17-world-daily-continuous-stay-and-plan-change.md)；
  UNKNOWN 闭环与营业事实通道见
  [2026-10-18-world-daily-unknown-closed-loop-and-fact-read.md](./2026-10-18-world-daily-unknown-closed-loop-and-fact-read.md)。
