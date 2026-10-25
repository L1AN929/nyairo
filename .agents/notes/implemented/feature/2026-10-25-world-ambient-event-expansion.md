# Agent Note: 世界环境事件目录扩充与有界跨天阶段

Status: implemented

> 跨天实现已于 2026-10-25 被有限事件替代（见下方「后续替代」）。本文其余决定与理由
> 保留为当时的历史记录，不再代表当前生产的 8 天周期重演。

## Problem

世界侧的环境事实只有两条真正接通观察的路径：

- 内建汐见市 `world_ambient_events._TEMPLATES` 的 **5 条**模板；
- 世界包 `ambient.json` 的模板（`daily_neighborhood_v1` 原来 **2 条**，schema 固定
  `event_type/location_id/summary/start_hour/end_hour` 五字段、上限 16 条）。

两条路径都经 `world_runtime_data.WorldData.ambient_templates` 汇合，
`observe_world` 只在 `CHIYO_WORLD_AMBIENT_EVENTS_ENABLED` 开启时投影当天事实。缺口是：

1. 目录太小，且没有类别元信息：日常小事、时间窗会变、跨天连载混在一起，无法说明
   “哪条只是环境叙述、哪条由时间驱动”。
2. 所有事件都是单日身份（`event_id` 直接带当天日期），**没有跨天阶段**：同一件事
   无法在相邻两天以同一身份继续，也无法在周期结束后自然到期；如果靠每天随机重抽
   同主题，那只是同主题的不同事件，不是接续。
3. 跨天若新增持久账本、任务或承诺合同，会越过 World 的事实边界，并可能被误读成
   “她答应了什么”或“用户收到了消息”。

## Decision

只沿既有环境事件投影扩内容，不新增持久 schema、不新增第二套 owner。

1. **目录扩到 24 条**（默认汐见市）：日常 8、会变 8、跨天 8，`event_type` 全局唯一。
   既有 5 条全部保留并归类；原文案不改。类别只是 `ambient_template_catalog()` 的
   目录元信息，**不进入事件投影字段**。
2. **跨天用周期锚点 + 阶段投影**，只复用既有事件字段：
   - 模板带 `phases`（`day_offset/start_hour/end_hour/summary`）、`period_days`、
     `period_offset`；
   - 对观察日 `D`，按 `(D.toordinal() - period_offset) % period_days` 求周期锚点；
   - 阶段事件的 `event_id` 用锚点日期，因此相邻两天是**同一身份**，`payload.summary`
     给出不同阶段叙述；周期结束后不再生成；
   - 下一周期锚点前移，`event_id` 必然不同，不重放、不把未理会事件永远吊着；
   - 跨天仍按 World 时区与调用方给出的业务时钟计算，不读全局墙钟覆盖业务 `now`。
3. **每日名额不变**：`MAX_EVENTS_PER_DAY` 仍为 3。跨天阶段优先占位（当前周期布局
   每天恰有 2 条），其余名额给单日模板轮转，所以目录 24 条不等于每次观察广播 24 条。
4. **包路径保持既有合同**：`daily_neighborhood_v1/ambient.json` 扩到 schema 上限
   **16 条**单日模板（仍五字段），并更新 `MANIFEST.json` 的 `ambient.json` 摘要。
   包路径的分类统一按日常处理，**不支持跨天阶段**；需要跨天必须扩
   `components/worldgen/schema.py` 的 ambient 模板字段，那不在本单写集内，属于
   具名缺口，不以包外代码硬塞事件绕过。
5. **事件仍只是环境事实**：payload 只有 `summary`；不写具体价格、库存、气象权威、
   NPC 承诺、用户收到消息，也不授予动作许可。会变类全部是时间窗事实，本单没有
   “真实动作改变事实”的条目（那需要真实 owner 承接，单列 unknown）。
6. 新增 `ambient_template_catalog(environment=None)` 只读目录视图（总数、三类计数、
   周期与阶段），供测试/工程目录使用；它不是事件实例，不产生候选。

明确不改：事件字段集合、每日上限、`observe_world` 返回结构、`worldgen` 包 schema、
World service/contract/db、Life/AG0/AG1/AG2/AR0/LR2/LR4、默认 World gate 与预算。

## 后续替代（2026-10-25 v2，范围限定）

本 note 的“8 天周期锚点 + 阶段投影”已被
[世界包 ambient v2 与有限跨天事件](../architecture/2026-10-25-world-ambient-v2-finite-events.md)
替代：内建 8 条改为世界包作者给定固定日期的一次有限事件（期限后不复活），
`daily_neighborhood_v1` 改为 v2（16 单日 + 8 有限，上限 24）。本 note 保留为当时的
决定历史，不再把周期重演称为生产跨天；v1 五字段读取、每日上限、地点视野与
“事件只是环境事实”的边界不变。

## Alternatives considered

- **给 `worldgen.ambient.v1` 加跨天字段**：让世界包也能直接表达跨天阶段，概念最统一。
  否决：`validate_ambient_doc` 强制模板恰好五字段，扩字段是新的包配置契约，且文件不在
  本单写集；本单只在能承载的内建路径实现跨天，并把包路径限制写清。
- **把跨天拆成多条独立单日模板、靠同主题每天重抽**：实现最省，不需要任何新元信息。
  否决：那不是同一事件身份，无法证明“今天/明天是同一件事的第二阶段”，只是每天重新
  编一条相似文案；也不会自然到期，属于任务书明确禁止的假接续。
- **放宽 `MAX_EVENTS_PER_DAY` 到 24**：目录能一次全广播，展示最直观。否决：破坏既有
  投影上限（条数/字节/视野边界），把“目录规模”误当成“每轮事实量”。
- **把修路/损坏/进度做成真实动作派生事实**：更接近“世界会因她变化”。否决：现有 ambient
  没有动作后果 owner，也没有可读的 World 动作状态合同；硬写就是捏造她修好了东西。
  本单只做时间窗叙述，动作派生列为未覆盖。
- **在 Life/phone 另建事件循环**：能绕开 World 模块的模板校验。否决：会产生第二个事件
  真相，与“World 是唯一事实来源”冲突。

## Consequences

- **收益**：世界第一次有 24 条可数的环境目录，三类可审计；跨天有稳定身份、阶段连续与
  自然到期，且不新增持久账本或 owner；包路径仍能按旧合同加载（16 条、五字段）。
- **收益**：跨天占位后每天仍有 3 条上限，不会把整城广播塞给观察者；地点过滤与不可达地点
  的既有拒绝不变。
- **代价与已知上限**：跨天阶段当前只在内建汐见市生效；包世界（含 `daily_neighborhood_v1`）
  仍是单日模板，真实使用包世界时看不到跨天连载。要补齐必须另批包 schema 变更。
  （以上为当时状态；包世界跨天已由同日 v2 补齐，见「后续替代」。）
- **代价与已知上限**：跨天每天占 2 个名额，单日日常/会变实际每天只出 1 条，16 天才轮完
  一轮；这是既有上限下的取舍，不是目录缺失。
- **代价与已知上限**：`components/worldgen/perception.py` 调用环境事件时传
  `allowed_location_ids={place_id}`，与 `materialize_ambient_events` 的
  “known 必须被 allowed 覆盖”检查冲突，异常被吞后环境事实恒空。这是既有缺陷，不在本单
  写集，未修；`observe_world` 路径不受影响。
- **三态归类**：有真实链证据的只是“正式 `observe_world`/事件观察入口 + 真实 World owner
  读口读到新目录与跨天阶段”；**半做**的是包世界跨天（schema 写集外）；**没做**的是
  真实动作派生会变事实、真模型自然采用、任何生活因果或 G1/24h 验收。

## Verification

- `tests/test_world_ambient_event_expansion.py`（11 例）：目录 24/8-8-8、唯一性、每日上限与
  字段；单日窗前/窗内/窗尾/窗后；东京午夜与 UTC 前一日；跨天同 `event_id` 连续两天、
  阶段文案不同、第三日到期、下一周期新身份；重复读稳定；地点过滤与未知地点拒绝；包世界
  16 条单日；包不可读保持 `WorldDataError`；模板无动作/后果/价格字段。
- `scripts/world_ambient_event_expansion_smoke.py`：隔离 root 用正式 `WorldStateStore`/
  `world_timeline` 建状态，逐日经 `materialize_ambient_events` + `observe_world` 读到
  日常 8、会变 8、跨天 8；跨天 `ambient-2026-10-24-park_lawn_rest` 在 10-24/10-25 连续、
  10-26 消失、下一周期 11-01 换新 id（上一版周期机制的历史验证，现生产已被有限事件
  替代，不再重演）；重复观察稳定；观察前后隔离 root 文件摘要不变。
- 直接相关回归：`components.worldgen.tests.test_runtime_wiring` 9 例、包校验
  `validate_package_dir(daily_neighborhood_v1) == []`。

相关决定：[只读机会目录](../architecture/2026-10-17-world-daily-opportunities.md)
（同一街区包与“事实不是授权”的边界）、
[街区 owner 链验收](../testing/2026-10-18-neighborhood-owner-chain.md)（包世界真实链路）。
