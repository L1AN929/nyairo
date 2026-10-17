# Agent Note: 非产出停留（stay）与"闭店改主意"由 Life 自己的 checkpoint 控制

Status: implemented

## Problem

W 包给出"此刻能去哪儿、开没开门、能做什么"，F 包给出"这一份能不能吃"，两者都能从一次真实
Decision 走到 owner。剩下的两块是空的，而且都不是"少写一个分支"能补上的：

1. **没有非产出停留**。要求的是"在公园坐一会儿 / 安静驻足"这种**有真实起止**的可选活动。
   World 侧没有对应 verb（`POSE` 要多参数，AR-0 的单 target 合同表达不了；旧版本会直接编一个
   `REST`/`SIT` 动作，或者拿一次 `NO_ACTION` 加时间跳当"已经玩过了"并宣称完成），两条都会
   伪造后果。缺的不是 verb，而是"她在一段时间里确实待在那儿、并且只有真的过了那段时间、
   世界仍把她放在那儿，才算完"这件事由谁记账。
2. **闭店之后没有可变计划**。MOVE 与营业已经解耦（闭店也能到门口），但"到门口后重读 owner
   当下的事实、不进店、改去别处或在原地待着"没有任何一处被真正驱动，也没有任何一处被证明。
3. 顺带暴露的两个产品问题：AG-0 的 World source 会把**上一刻**的机会继续留在候选里直到有效期
   结束（长一点的一天就把 AG-1 的 16 条候选上限占满）；Life 侧给自己的"营业时刻"和"现在时刻"
   只能是同一个墙钟，无法既确定又真实。

## Decision

### 停留是 Life 自己的有界 step，不是 World verb

`world_move_opportunity_source.observed_stay_opportunities()` 在**她真的所在、且 owner 说此刻
开着**的地方产出唯一一条 `stay:<place>:<descriptor>:<seconds>` 机会：

- 描述符与时长来自该地点自己列出的 affordance（`STAY_DESCRIPTOR_PREFERENCE`：SIT/SIT_AT_CAFE/
  REST/READ_HERE/READ/USE_PHONE/BROWSE_BOOKS/LOOK_AROUND，最后才是 WALK），时长是模块里的固定
  表（SIT 300s、READ_HERE 600s、LOOK_AROUND 180s…）；没有已知 affordance 时退化为
  `QUIET_STOP` 180s，绝不冒充店里或公园自己的 affordance；
- catalog 读不到、地点不在其中、或该地点此刻不是 `OPEN` → **一条也不产出**（fail closed），
  "读不到"永远不变"就在这儿待着"。

Life 侧的 step 是 `ag2.STEP_KIND_INTERNAL_CHECKPOINT` + `action_kind=None`：AG-2 记录 step、
**不产生 Action**（`life_integration_ag2.create_step_and_propose_action` 里 action_kind 为 None
就只落 step 并返回），因此没有提交栅栏、没有一次性授权、没有回执——停留本来就不改世界状态。

Activity 形状 `STAY_AT_OBSERVED_PLACE` 只用 LR-5 既有词汇：`WORLD_ACTION_BOUNDED` +
`LATEST_POSITION` 进度合同，完成判据是 `WORLD_FACT_MATCH`，`fact_key=world_stay_ref`、
期望值是 `world_fact:stay:<place>:<descriptor>:<seconds>`。这个期望值在 adoption 时就已经确定
（计划里带着地点与时长），而证据只在**两个事实同时成立**的那一轮才写入它：
`_classify_stay_step` 每轮都重新经真 gateway 读一次"她在哪儿"，并且用 LR-5 自己记下的第一个
checkpoint（`CheckpointUpdate.occurred_at`）算已过秒数。于是：

- 时间没到 → 证据不带 `world_stay_ref`，`progress_state` 不是 `COMPLETION_ELIGIBLE`，Activity
  仍是 ACTIVE、前台仍被它占着（一轮 `ONGOING`）；
- 时间到了但世界把她放在别处 → 拒绝并如实写明 `WORLD_POSITION_DOES_NOT_MATCH_STAY`，不写证据；
- 两个都成立 → 才提交完成证据，LR-5 判 `COMPLETION_ELIGIBLE` 并结束 Activity、释放前台。

### 停留期间不提供第二条前台路径

`evaluate_once` 先看前台是不是一个 stay Activity（`_foreground_stay_activity()`，从 Activity
owner 读 `activity_kind`）。是的话这一轮的 step 只能是 stay 的 checkpoint，AR-0/阅读 step 一律
不提供——单前台是 AG-2 的规则，这一层只是不把不可能合法执行的东西递上去。

### 闭店之后的可变计划由 owner 的当下事实上桌，不由固定脚本

一次观察同时给出三类机会（`place:` / `object:` / `stay:`），全部来自 owner 自己此刻的
action space、可用性投影与 W 目录；闭店的店仍在单跳候选里（门口走得到），但它的
`affordances` 为空、`observed=not_observed`、没有 `stay:` 也没有 `object:`。于是"改主意"就是
一次正常的 Decision：读到闭店事实后不进那一支，改在本地安静待着或走开——没有为了"全目的地"
在 Life 侧造一张全局地图，也没有把闭店写回出发条件。

### 两个显式时刻，各管各的事

`WorldMoveOpportunityProvider` 新增 `observed_now`：`now` 只用于读 W 目录（营业事实），
`observed_now` 是这次观察发生的时刻（机会 id / 有效期 / 到达与停留的计时都用它）。两者缺省
相同（生产形状）；演练里把目录钉在一个真实的周六中午（店的"工作日 8–18"于是确定闭店、
公园 6–22 确定开着），Life 时钟仍贴着墙钟走，World owner 自己按墙钟算的
`expected_arrival_at` 才继续有效。

### 上一刻的机会会被过期，而不是一直占着候选

`AutonomousLifeCycle._refresh_world_opportunities` 记住本轮喂给 AG-0 的机会 id，对**本轮观察
不再支持**的 id 调 AG-0 自己的 `expire_opportunity(oid, observed_at=...)`。这条不改任何候选
语义，只把"上一刻的门牌"从候选集里收走：没有它，一天走得越久，AG-1 的 16 条候选上限越容易
被没人能选的机会占满（本批演练实测到 `DECISION_ATTEMPT_FAILED:CANDIDATE_EXPLOSION`）。

## Alternatives considered

- **给 World 加一个 `STAY`/`DWELL` verb** — 表面最统一：一次 Decision 就能"停一会儿"。
  否决：World owner 的动作语义是"改世界状态"，而停留不改任何世界状态；加 verb 要同时动
  AR-0 的 verb 集合、桥的两份 allowlist、journey 的委派与 World 的 Resolver，等于为一个
  **不该有副作用**的行为新开一条有副作用的通路，还要为它编一份回执。
- **用 `POSE`（坐下）当停留** — 不改白名单，`POSE` 也确实改姿态。否决：AR-0 的单 target 表达
  不了 `POSE` 的多参数；更要紧的是姿态变化不证明"过了 300 秒还在那儿"，完成判据会退化成
  "她现在坐着"，正是要禁止的伪造。
- **拿 `NO_ACTION` + 时间跳当"玩过了"** — 零改动，报告也好看。否决：那样完成是模型说出来的，
  没有任何 owner 事实支撑，也没有 checkpoint；一次 `NO_ACTION` 轮就会变成一段虚构的经历。
- **让 Life 侧记一个"停留计划"小账本** — 实现最直接。否决：计划已经在 `stay:` target 里
  （地点+描述符+秒数），Activity 的 `subject_refs` 与 LR-5 的 checkpoint 都存着它；再开一份
  账本就是第二个真相，重启后两处必然漂移。
- **接受"每次观察的机会都留在候选里"** — 不改任何东西，靠有效期自然过期。否决（实测）：
  有效期按注入时钟算，长链里上一刻的 3–4 条机会会持续与当前机会竞争，把 AG-1 的候选上限
  顶爆，报出来的是 `CANDIDATE_EXPLOSION` 而不是"她没什么可选的"。
- **把营业时刻和 Life 时刻都钉成周六中午** — 单一注入时钟，故事最干净。否决（实测）：
  World owner 用自己的墙钟写 `expected_arrival_at`，一个"一周后的现在"会让出发/到达的结算
  与 owner 的记录互相打架；两个**显式声明**的时刻各自负责一件事，反而没有假时钟。

## Consequences

- **收益**：一天里第一次能有"她真的在某个地方待了一会儿"的记录：Activity 由 AG-0/AG-1/AG-2
  合法产生，checkpoint 由 LR-5 记，完成由"世界仍在那儿 + 声明时长真的过了"两个事实共同判定；
  停留不产生 Action、回执、身体变化或营养事实。闭店后的计划变化同样走真实候选，而不是脚本。
- **收益**：候选集不再被上一刻的机会占满；演练里候选数稳定在 7–9 条（上限 16）。
- **代价与已知上限**：停留时长是模块里的固定表，不是配置；换街区/换世界包要一起改。
  `stay:` 的解析依赖 `<place>:<descriptor>:<seconds>` 这一种拼法。
- **代价与已知上限**：`observed_now` 与 `now` 分离是给"要确定性又要真实结算"的演练准备的；
  生产里两者相同，因此没有第二个时钟的复杂度。
- **代价与已知上限**：本笔记里的"世界仍把她放在那儿"用的是这次 gateway 读到的
  `place_id`；室内 area 级别的停留（"在卧室而不是客厅"）今天不区分。
- **已知缺口（owner 侧，未修）**：World 的计时出行在"同一墙钟秒内连续多次出发 + 结算时钟在
  前面"时会偶发地把一次真实出发答成 `UNKNOWN`（journey 落成 `blocked/UNKNOWN`，travel
  `IN_PROGRESS`）。本批不修 W/F；演练先对账（`settle_world_journey`）再决定，见
  `docs/engineering/world-daily-i-r1-result.md` §6。

## Verification

- `tests/test_world_daily_life_play.py`（4）：公园中午 OPEN → `stay:park:SIT:300`；周六 06:00
  咖啡店 `CLOSED/WEEKEND_CLOSED`、affordance 空、无停留机会、门口仍可走；公园 23:00 无停留；
  目录不可读时一条都不产出。
- `tests/test_world_daily_life_continuous.py`（1，同实例连续演练）：真实 owner 上 停留下
  `START → ONGOING（120s<180s，未完成）→ END（240s≥180s，COMPLETION_ELIGIBLE，前台释放，
  checkpoint `world_fact:stay:street_front:LOOK_AROUND:180`）`，以及闭店事实下改主意的
  同一轮候选证据。
- `tests/test_world_daily_life_consume.py`（6）保持全绿（同一 provider 的 MOVE/CONSUME 两路
  不受影响）。
