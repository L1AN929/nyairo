# Agent Note: 世界活动可选的标题与建议秒数（只覆盖已有活动的展示与计划建议）

Status: implemented

> 2026-11-07 后续裁定：本笔记中「未知 affordance 仍走 QUIET_STOP 兜底」这一点已被
> [strict-activity-definition](2026-11-07-strict-activity-definition.md) 替换——没有可执行
> 停留定义时不再自动补「待一会儿」。标题/秒数覆盖、legacy 绑定与其余结论仍然有效。

## Problem

世界包的 `rules.json` 里，一个 affordance 只能写 `affordance_id` 与 `description`；名字与时长分别
由 Life 的两张表决定：`STAY_TITLES` 给 Activity 起名，`STAY_SECONDS_BY_AFFORDANCE` 决定停留多久。
结果同一个 `READ_HERE` 在任何地点都叫「在这里读一本书」、都待 600 秒，地点自己没法说「我这里的
读书位大概适合坐十分钟」或「这扇窗边适合歇一小会儿」。模板与生成器也没有任何位置能表达这件事。

需要的是**有限扩展**：让世界包为它已经在支持的活动补一条展示标题和一条建议时长，而不是让新的
affordance 因此获得执行资格。准入、排序、quiet 兜底、授权与请求预算都保持原样。

## Decision

1. **契约**：`rules.location_rules[*].affordances[*]` 在原有必填 `affordance_id`/`description`
   之外，允许两个彼此独立的可选键：
   - `title`：提供时必须是非空、去首尾空白后 1..80 字符的单行纯文本；换行、控制字符、非字符串
     一律 `WorldPackageError`。只作展示，不进入 target、身份或幂等键。
   - `suggested_seconds`：提供时必须严格是 `int`（`bool` 不算），范围 1..86400。既有 stay 计划
     只校验 `>0`、没有上限，因此新字段采用明确写死的 1..86400，不抬高任何既有上限。
   缺哪个键就少哪个键，不补默认；显式 `null`/空串/错误类型/越界都拒绝，不当成缺失。未知键继续
   拒绝，`set(item)` 的严格性没有放宽。`schema_version` 仍是 `worldgen.rules.v1`，这是批准的
   可选键扩展，不是强制迁移。
2. **模板与生成**：`coastal_town.json` 的模板 affordance 仍以 `[id, description]` 为主；需要元数据
   时写第三个对象元素 `[id, description, {"title": ..., "suggested_seconds": ...}]`。`coastal_town`
   的 `home` / `REST` 是现有支持活动的最小示例（`在窗边歇一会儿` / 240 秒）。生成器把两种形式
   归一成带可选键的 rules affordance，随后与其它文档一起过正式 schema 自检。
3. **运行时透传**：loader 把 affordance 归一成元组：旧包仍是 `(id, description)`，只有 title 是
   3 元组，只有秒数用 `None` 占位成 4 元组。`world_daily_rhythm.affordances_for_location` 与
   `world_daily_opportunities` 的目录只在世界包真的声明时带上这两个键；`compact_projection` 仍只
   投影 `affordance_id`，不扩大短视图。
4. **Life 侧的使用边界**：`world_move_opportunity_source._stay_descriptors` 只在 descriptor 同时
   在 `STAY_DESCRIPTOR_PREFERENCE` 与 `STAY_SECONDS_BY_AFFORDANCE` 中时，才用**同一地点同一条
   affordance** 的 `suggested_seconds` 覆盖默认秒数；选择顺序、idle 轮换、`QUIET_STOP` 兜底都不变。
   未知 affordance 即使带齐元数据也不会获得停留资格。标题只在 `opportunity_view` 里按地点+
   descriptor 精确匹配取得，缺失即 `None`，不同地点不会串用；`life_live_context_view` 有标题时用
   标题当 label、没有时保留原 `AFFORDANCE (seconds)`。Activity 标题由 cycle 从同一 target 的视图
   取，缺失才回退 `STAY_TITLES`。**秒数是原 target 编码的一部分**（`stay:home:REST:240`），覆盖后
   会进入 target；只有 title 不进 target、身份或幂等键，计划不会漂移。
   语义上 `suggested_seconds` 是「这个地点建议待多久」，选中后仍按原 target/plan 执行；PAUSE /
   ABANDON 等出口不变，也不存在必须待满的硬约束。

## Alternatives considered

- **把元数据塞进 target（如 `stay:<place>:<descriptor>:<seconds>:<title>`）。** 最强立论：一次
  解析就能同时拿到标题和计划，不必再回查目录。否决原因：title 会变成身份与幂等键的一部分，标题
  一改就是新候选、新请求，还会让 `parse_stay_target` 的既有三段落约定失效；这正是本次明确禁止的
  「把 title 写进身份」。
- **让带 title 的未知 affordance 直接进入候选。** 最强立论：世界包既然能描述它，就说明地点真的
  能做，Life 不该再维护白名单。否决原因：那会改变 World→Life 的准入合同，把「元数据描述」偷换成
  「动作授权」，并让排序与 quiet 规则失去意义；本次只批准内容覆盖，不新增活动准入。
- **把 title 提成 rules 顶层或另建 schema 文件。** 最强立论：结构更干净，未来可扩更多字段。否决
  原因：现有 bundle 就是 `[id, description]` 平铺形式，另开一张表会让旧包与新包走两条读取路径，
  也和「最小兼容扩展」相反。
- **用 `description` 或模型正文推断时长/标题。** 最强立论：零 schema 改动。否决原因：从自由文本
  解析执行参数不可验证，也会把展示文本变成计划来源；拒绝。

## Consequences

- 世界包现在能说「这个地点的这个已有活动叫什么、大概待多久」，且只影响展示与**建议**秒数；
  选中后的计划仍是 `stay:<place>:<descriptor>:<seconds>`，PAUSE/ABANDON 等出口不变。
- 旧世界包与旧模板一个字节都不用改：缺字段时逐字段回退到原标题/秒数表，候选 id、descriptor、
  顺序、quiet 兜底与旧基线一致；不同地点同名 descriptor 的元数据互不污染（有负控用例）。
- 新增/未知 affordance 仍不能仅凭 title/秒数获得停留资格；要真正支持一个新活动，仍得改 Life 的
  偏好与时长表——这份扩展不宣称消除那部分维护。
- 验收：`tests/test_affordance_optional_metadata.py` 4 个聚合用例覆盖「真实生成世界→正式目录/
  观测→AG-0 候选→`ProductionAgencyCognitionAdapter.invoke`→正式 `AgencyModelClient`→最外层
  script transport 实收 user JSON（同地点 target、展示 title、240 秒、NO_ACTION）」、同一份
  scratch world 经正式 runtime/cycle 的 AG-1 → AG-2 创建 canonical Activity（title 为配置值、
  `world_fact:stay:home:REST:240` 秒数一致，不执行世界副作用）、旧包/部分字段回退与跨地点负控、
  schema/模板非法值拒绝与未知 ID 准入负控、其它消费者与纯文本标题安全。真实模型是否自然选择
  这些选项、浏览器 DOM 是否安全不在本笔记的验证范围。
- 相关既有决定保持有效、未被翻转：[idle-and-dawdle](2026-11-05-idle-and-dawdle.md)、
  [world-daily-opportunities](../architecture/2026-10-17-world-daily-opportunities.md)。
