# Agent Note: v0.6 既有 ambient 事实进入 Life 背景

Status: implemented

## Problem

2026-11-02 的隔离 check 已经证明：World 正式 `observe_world` 在 home/bedroom、当前时段下
能看到 `ambient-2026-11-02-home_window_sound`，但 Life 的 `daily_life_context`、
`situation_context.world`、AG-0 候选和正式 `build_prompt` 全都没有这条事实的 ID。
网关 `get_snapshot/get_observation` 当时没有 `ambient_events` 专属字段，Life provider 的
被测快照路径也未保留事件ID。原检查只查ID，未查summary或同义文本，不能据此证明完整环境
信息绝对没有进入Life，更不能断言她选择忽略。本次补的是明确、有界的环境事实背景通道。

本次批准的范围是**限定补接**：只把既有、已由 World 投影出来的当前地点/时段 ambient
事实带进 Life 背景；不新增候选、任务、动作许可或行为规则，默认开关不变，不实跑产品模型。

## Decision

1. `WorldMoveOpportunityProvider.daily_life_context` 新增一个受控的 `ambient` 背景块：
   仅当实例环境里 `CHIYO_WORLD_AMBIENT_EVENTS_ENABLED` 已启用、且能绑定到同一个
   `CHIYO_WORLD_PACKAGE` / world home 时，才走**正式** `world_living_skeleton.observe_world`
   读取 `ambient_events`。时间窗、地点可达集合、每日上限（3 条）都由 World 自己完成；
   Life 不重新解析世界包、不自己判断事件是否有效。
2. 这次读取把 `CHIYO_WORLD_TRAVEL_TIME_ENABLED` 只对本次调用设为 `0`：`observe_world`
   在正常路径会顺手兑现到期旅行（一个 World 写动作），背景读取不能从 Life 进程写
   World 状态。因此官方读口保留，副作用关闭。
3. 事件行只保留公开身份字段 `event_id / event_type / location_id / local_date /
   valid_from / valid_until` 与 `payload.summary`；summary 只 strip + 长度上限，保留原文，
   以便读者还能认出同一条公开模板。读取失败返回 `status: unavailable` 与具名
   `reason_code`，绝不补造事件。
4. `life_live_context_view` 在 `_bounded_contract` 与 `bound_for_prompt` 两处按同一白名单
   透传 `ambient`，条数上限仍为 World 的每日 3，其余键全部丢弃；关闭/缺失时完全不出现
   该键，旧形状不变。
5. `agency_cognition_adapter` 不需要新逻辑：它已经通过 `bound_for_prompt` 复制 world 背景，
   `ambient` 随正式 prompt 进入 `situation_context.world`。没有新增任何鼓动行动的文字。
6. 隔离 driver 在原有 off/on 场景上延伸：World 观察里的事件 ID **和**公开 summary 必须在
   `life_situation_world`、`life_live_context`、正式 prompt 结构及 prompt 文本中同时精确
   出现；off 三个层都不得有 `ambient` 键；AG-0 候选不得出现事件 ID；候选 ID 两边一致。
   网关 socket 与 gateway-snapshot 层仍如实报告为「另一条读口」，不算承载层。

实测（2026-11-02，最终入口，19.705 秒，新根 `/tmp/nyairo-v06-ambient-offline-1_4_3c0k`）：
off world 0 条、prompt 8127 字符、world/live/prompt 均无 ambient；on world 1 条
`ambient-2026-11-02-home_window_sound`，三层都有该 ID 与 summary，prompt 8492 字符未裁剪；
off/on 候选 ID 完全相同，AG-0 无事件 ID；`result: PASS`。这里的 prompt 由正式 adapter
在捕获脚本 Decision 请求之后构造，**没有发往模型**，所以只证明接线与结构，不证明模型反应。

## Alternatives considered

- **扩网关 `get_snapshot/get_observation` 投影 `ambient_events`**：最强的理由是它保持
  「观察与背景来自同一次网关读」，不会多一次 World 观察，也不会出现两个读口的状态差异。
  否决：这会给既有 socket 协议新增字段/命令，属于对外协议变更，本单明确没有这项批准；
  为了一个背景块扩协议，收益不抵合同风险。
- **在 Life 里直接读世界包并调用 `active_ambient_events`**：最省，不需要 `observe_world`
  的完整投影，也不会触发旅行兑现。否决：那会让 Life 自己组装「当前 + 可达地点」的过滤集合，
  等同于在 Life 侧维护第二份世界过滤逻辑；以后 World 调整地点/时段规则时两边会漂移，
  也绕开了 World 自己的 observation 入口。
- **原样调用 `observe_world`，不关旅行**：代码最短，完全复用官方读口。否决：`observe_world`
  在 travel 开启时会兑现到期到达，那是 World state 写入；背景读取由 Life 进程发起，
  不应产生第二个写者。只对本次读取关闭 travel，既保留官方入口，又不写世界。

## Consequences

- 收益：默认关闭时 Life 一行旧代码路径都不变（off 仍是 8127 字符、无新字段、无额外读取）；
  显式开启后，当前地点/时段的公开环境事实第一次进入正式 `build_prompt`，并且 event id 与
  summary 在结构层和最终文本里都可精确核对。
- 收益：事件仍只是背景——候选、合法决策集、gate、排序和资格都没有变化；隔离检查同时
  断言候选 ID 相等、AG-0 无事件 ID、gate off。
- 代价：开启后每个决策轮多一次 World 投影读取（仅 ambient 启用时）；读取失败会显示
  `AMBIENT_READ_FAILED` 之类的具名未知，而不是静默缺失。
- 边界：只覆盖 C 实际加载的 `worldgen.ambient.v1` 6 条单日模板；不覆盖有限跨天事件、
  包外世界、真实模型自然采用、行动率变化、G1 或 24h。`ambient=1` 不是开门，也不证明
  她会因为「窗外有汽笛声」而改变选择。
- 失败史：新增测试首轮有一条用例把 World 原始事件形状（summary 在 `payload` 里）直接喂给
  view 的再裁剪层，导致 `events` 缺失；改成 provider 已展平的形状后通过。原隔离 driver 的
  三轮装配红与父级限制保留在 testing note，不倒写。

## 父级独立验收

父级按任务书两条入口各独立一次：新模块8例0.228秒OK；隔离check exit0/PASS、15.850秒，新根 `/tmp/nyairo-v06-ambient-offline-c11d_ple`，冻结2026-11-02 23:22:13 JST。off0条且背景无ambient键；on1条的同ID及公开summary均出现在world背景、live context、生产build_prompt结构及文本。off8127/on8492字符，均未裁剪；四个候选ID与本次合法集两边一致、AG0无环境事件候选、gate off。

本次请求仍由脚本cognition捕获后再调用生产build_prompt构造，未做到脚本client边界捕获，未送真实模型；不把“已构造最终输入”说成“已发送/已理解”。8例含World投影替身，隔离check才用本C包的正式World观察。旅行副作用关闭的依据为worker具名说明及单元参数断言；没有额外注入在途到达来证明全部World写面零写。正常关闭与原验证边界相同，不冒极端超时或全线程排空。产品预算212/300余88不变，默认关闭、旧根未写、未提交。

## 双读一致性（2026-11-02，相邻检查）

背景读 `observe_world` 与位置/动作空间读（网关 `get_snapshot`）是两条读取。新隔离、非冻结
真钟下实测：`world_id`、`place/area`、`revision` 两边一致；当前 home 有 1 条
`ambient-2026-11-03-home_window_sound`，其有效区间覆盖观察时刻；事件 ID 与公开 summary
在 world 背景、live context 和生产 prompt 三处精确一致。Life 的 ambient 块不自带
`observed_at`，这一侧的有效期核对如实记 UNKNOWN，不补原子快照。

负控暴露了既有漏接：第二条读若返回**另一世界**、**另一地点**或**已失效记录**，`_ambient_context`
原先仍会把它当“当前”交给背景。现补最小的同源/时效守卫：仅当两条读取都已报告的
`world_id`、place（area 只在 World 明确给出区域节点时比较）或事件有效区间互相矛盾时，
把该次背景读报成 `status: unavailable` 并给出 `AMBIENT_WORLD_MISMATCH` /
`AMBIENT_LOCATION_MISMATCH` / `AMBIENT_EVENT_WINDOW_MISMATCH`，不输出事件。缺字段仍是
UNKNOWN，不当作矛盾；没有逐条过滤事件，也没有在 Life 里重新解析世界包或新增字段/协议。

证据：`tests/test_v06_ambient_consistency.py` 6 例（含 3 个 TEST_INTERLEAVING 端口替身负控）；
`scripts/v06_ambient_consistency_check.py check --offline` 一次实际 exit0/PASS、6.944 秒，新根
`/tmp/nyairo-v06-ambient-consistency-gjo37jn2`。负控只证过滤/未知分支，不代表真实世界移动或
自然并发；未调用产品模型。缺少身份或时刻字段的负控仍返回available并透传事件，只证明未知不被判成矛盾，不证明已验证同源或时效；不能把“没有明确冲突”说成“新鲜性已证”。driver的source_basis里关于local_time被复制进Life块的说明与实际Life observed_at缺失不一致，以实际字段为准。

worker另自报修后追加原8例合计14例回归，超出本单不重跑旧绿的约定；不作为父级独立验收证据，父级仅固定新check一次。

父级固定新check实际exit0/PASS、12.126秒，新根 `/tmp/nyairo-v06-ambient-consistency-dya4w8_d`，真实收集6例、0fail/error/skip。当前World当地日期已是2026-11-03，home事件ID和公开summary三层一致，已知world/place/area/revision相符。Life块自身观察时刻仍null，`life_events_current=true`仅表示未见明确矛盾，不能当作该侧时效已证；未知负控也不是“unknown已明确显示给模型”的证明。正常World投影使用实时datetime.now，但gateway复用测试clock并在evaluate前set_now一次，不是持续推进的真实服务钟；不称已完成非冻结端到端或跨守护进程自然并发验证。请求仍捕获后构造prompt，不是实际发送；正常关闭沿此前边界。累计额度已由本人直接改为1000，保守占212/余788，此节前面的212/300为历史验收口径。

相关：[v0.6 ambient 隔离 off/on 分层检查](../testing/2026-11-02-v06-ambient-isolated-check.md)、
[世界包 ambient v2 与有限跨天事件](../architecture/2026-10-25-world-ambient-v2-finite-events.md)、
[世界环境事件目录扩充](../feature/2026-10-25-world-ambient-event-expansion.md)。
