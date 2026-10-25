# Agent Note: 世界包 ambient v2 与有限跨天事件

Status: implemented

## Problem

旧 `worldgen.ambient.v1` 的模板固定五字段（`event_type/location_id/summary/start_hour/end_hour`），
每个事件都是单日身份；世界包无法表达“同一件事在相邻两天以同一身份继续”。
上一版内建汐见市用 **8 天周期锚点**模拟跨天：周期结束后下一周期换新锚点，本质仍是
定期重演，不是一次有限事件；世界包路径则完全没有跨天。

需要一种明确、可版本化的格式，让世界包作者声明固定身份、明确起止与世界日期阶段的
有限环境事件，同时不破坏旧 v1 包的读取语义。

## Decision

1. 新增显式文档版本 `worldgen.ambient.v2`（`schema.AMBIENT_SCHEMA_V2`）。v2 每条模板
   必须带 `kind`：
   - `single_day`：五字段单日模板，与 v1 语义相同；
   - `finite`：有限跨天事件，带 `finite` 子结构。
   未知版本、缺版本、未知 `kind`、v1 文档携带跨天字段一律明确拒绝。
2. `finite` 子结构固定五个键：`identity / starts_on / ends_on / end_reason / phases`。
   `phases` 每条是 `local_date / start_hour / end_hour / summary`，日期必须落在起止内且
   严格递增。身份由世界包作者指定，事件投影的 `event_id = ambient-finite-<identity>`，
   不从观察日、进程启动日或周期偏移滚动生成。
3. 时间域使用世界包 `world.json` 的权威时区；v2 文档可选 `timezone`，但必须与
   `world.json` 一致，否则拒绝加载。不使用 `date.today()` 或全局墙钟替代调用方业务时刻。
4. 兼容方向是单向的：新读端向后兼容 v1（五字段仍按单日模板解释、行为不变）；旧读端
   遇到 v2 明确拒绝，不静默降级。v2 上限 24 条，v1 上限仍 16 条。
5. 默认汐见市目录把上一版 8 条周期模板**替换**为 8 条有限事件（固定日期
   2026-10-24～2026-11-01）；目录仍 24 条（日常 8、会变 8、有限跨天 8）。
   按作者日期，首日 10-24 与末日 11-01 各只有 1 条处于阶段中，中间日期每天最多 2 条
   （本组恰为 2 条）；实际可见还受各自小时窗与地点筛选，不是全天可见。
   上一版周期定义不再保留为生产跨天，原 note 只标注替代范围。
6. `daily_neighborhood_v1/ambient.json` 改为 v2：16 条原单日模板（加 `kind`）+
   8 条有限跨天事件；目录 24 条，`MANIFEST.json` 用既有工具重算。其他世界包不改。
   包目录分类是 daily 16 / changing 0 / cross_day 8，与内建的 8/8/8 不同；包内单日
   模板不设“会变”类别承诺。包内 8 条有限事件的 identity 与日期独立于内建
   （如包 `street_front_drainage_20261024` 与内建 `street_drainage_notice_20261024`
   不是同一条），同样首尾各 1 条、中间最多 2 条处于阶段中，并受小时窗与地点筛选。
7. 每日投影上限仍为 3，地点过滤、事件九字段与 `payload.summary` 边界不变；有限事件
   到期后不出现，事后 +8/+16 天或远未来都不复活。事件仍只是环境事实，不产生 Life
   资格、动作许可、价格/库存权威或 NPC 承诺。

明确不改：`observe_world` 返回结构、事件字段集合、每日上限、World service/contract/db、
Life/AG0/AG1/AG2/AR0/LR2/LR4、默认 World gate 与预算；也不迁移旧实例或旧世界包。

## Alternatives considered

- **继续用 8 天周期锚点**：实现最省，不需要版本升级。否决：周期结束后会再次出现同一主题
  的新身份，属于定期重演；用户与最新裁定都明确“同一件有限事项”才算跨天，周期不是本目标。
- **在 v1 里直接加 `finite` 字段**：包文件改动最小。否决：旧读端只做“恰好五字段”校验，
  会直接拒绝带新字段的包，无法区分“旧包”与“新格式”；没有版本标识也无法明确拒绝 v2，
  容易产生静默误读。
- **按观察日自动平移作者日期**：能保证“她总有事”。否决：那是把环境内容伪装成每次运行
  都新鲜的私有事项；任务明确禁止通过准备/重启平移日期。
- **为有限事件新建 owner/账本**：可以做复杂的阶段推进与通知。否决：环境事件是可确定性
  重算的只读投影，新增持久账本会成为第二事实来源，并可能被误读成她的个人承诺。
- **放宽容纳上限到 24 并全城广播**：展示直观。否决：破坏既有每日 3 条与地点视野边界。

## Consequences

- **收益**：世界包第一次能声明真正的有限跨天事件；同一身份跨日稳定、阶段可变、期限后
  不复活；v1 包与内建单日行为不变；旧读端不会被新格式误导。
- **代价**：v2 与 v1 不能混用同一文档；包作者必须显式写 `kind` 与绝对日期；v2 上限 24。
- **代价**：内建 8 条有限事件是作者给定的固定日期内容，过了 2026-11-01 默认世界不再有
  跨天事件；这是内容日期，不是通过运行日滚动保证“总有事”。
- **已知边界**：未证明真实模型自然采用、生活因果、G1 或 24h；环境事件不产生 Life 资格。
  旧 `perception.py` 的地点过滤自述缺陷未在本单修改。

## Verification

- `tests/test_world_ambient_v2.py`：版本分派/未知版本拒绝、旧读端拒绝 v2、v1 字段严格性、
  v2 kind/identity/日期/阶段/时区约束、重复身份、超限、真包跨天同身份、跨午夜窗口、
  到期与 +8/+16/远未来不复活、清缓存重建稳定、重复读不写包、无动作权威字段。
- `tests/test_world_ambient_event_expansion.py`：内建 24 条三类计数、单日窗口、地点过滤、
  v2 包 16 单日 + 8 有限、随仓 v1 包仍加载、改前 v1 fixture 与固定 golden 逐日一致。
- `scripts/world_ambient_v2_smoke.py`：隔离 root 经 schema/loader → 正式
  `observe_world` 读到 3 个有限事件日 1/日 2 同 id、期限与 +8/+16 消失、
  重建读端稳定、零写、旧 v1 fixture golden 一致；exit 0。
- `scripts/world_ambient_event_expansion_smoke.py`：内建 24 条、8 条有限跨天经正式观察
  连续两天、第三日与 +8/+16 不复活；exit 0。
- A 组（v2 + expansion + daily_opportunities + runtime_wiring）：**父级独立实跑 56 例
  13.029 秒 exit 0**，日志 `.agents/worker-runs/20261025-world-events-v2-parent-a.log`。
  worker 首交写 55 例，与父级 56 例不一致，差异未逐例归因，以父级实跑为准。
- B：**父级独立实跑** `scripts/world_ambient_v2_smoke.py` exit 0 / PASS，日志
  `.agents/worker-runs/20261025-world-events-v2-parent-b.log`。
- 兼容层须分开看：旧版 golden 逐日比对属 fixture 层
  （`tests/fixtures/world_ambient_v1_legacy_golden.json` 是非空固定条目，由
  `tests/fixtures/_gen_ambient_v1_golden.py` 生成），与正式 `observe_world` 不同层。
  B 回执 `legacy.observe_ambient_count=0` 表示正式旧读口在该位置/该时刻读到零事件，
  不是“展示了非空旧事件”；golden 非空比对是否存在由固定 golden 与交付决定。
  golden 是否真正执行改前代码生成仅为 worker 声称，父级在现树比对通过，未独立重建
  旧源码 baseline，不能据此证明历史同一性。旧读端拒绝 v2 仅为测试覆盖，未在历史
  安装版现场验证。

相关：[世界环境事件目录扩充](../feature/2026-10-25-world-ambient-event-expansion.md)
（上一版 24 条与 8 天周期；其中跨天部分由本 note 替代）、
[只读机会目录](../architecture/2026-10-17-world-daily-opportunities.md)。
