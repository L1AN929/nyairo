# Agent Note: 严格活动定义层（REST / READ_HERE 显式 `stay_at_place`）

Status: implemented

## Problem

2026-11-07 17:25 本人当窗拍板：「漏写静默变成『待一会儿』我明确不要，必须改成报错；
不用等全套定死，按你们刚理出来的三层把关边界，先挑最简单的一两种动作把第一层做出来。」

旧行为有两处把"不知道"伪装成"成功"：世界包只给 `affordance_id`/`description` 时，Life
用两张内部表决定标题与秒数；表里没有的 ID 会被静默忽略，最后 `_stay_descriptors` 补一条
`QUIET_STOP`（180 秒、标题「待一会儿」），看起来像她自己选了"待着"。世界包也没有任何结构
能说明"这条 affordance 是一个可执行停留定义"还是"只是描述环境/物件"。

本单只做三层里的第一层——**定义成立与否**，并把现有消费路径上的错误变可见。第二层
（此刻时间/营业/权限是否允许）和第三层（AG-0/Decision 是否授权执行）不动，也不因为第一层
通过就发 grant 或绕过 AG-0。本次唯一机制 `stay_at_place` 引用的是已经实现的原地停留计划，
不是新动作，也不是权限；`READ_HERE` 不因此声称真的取得书页或完成读书。

## Decision

1. **可选 `execution` 对象**：`rules.location_rules[*].affordances[*]` 新增可选键
   `execution`，本版唯一合法形状是 `{"mechanism": "stay_at_place"}`。一旦出现：
   `mechanism` 必填且只能这个值；`title` 与 `suggested_seconds` 必须同时明写并符合上一单
   的 1..80 单行纯文本 / 严格 int 1..86400；拒绝未知键、未知 mechanism、`null`、空值、
   类型错。只有 `REST` 与 `READ_HERE` 可以显式引用；其它活动显式声明得到固定
   `UNSUPPORTED_ACTIVITY_DEFINITION`，不是准入。不增加 weight / conditions DSL / grant /
   subject / 脚本或路径字段，也不从 title/description 自由文本解析执行参数。
2. **单一权威合同**：新增 `components/worldgen/activity_definition.py`，集中定义机制名、
   受支持活动集合、固定错误码与 `validate_execution`。`schema.py`、`generator.py`、
   `loader.py` 与 Life 的 runtime 读取都只消费这一份合同。生成器模板第三元素允许
   `execution`；loader 元组在 title/seconds 之后追加第 5 段（显式 execution 必然带齐前两者，
   旧包仍是 2/3/4 元组）；World 目录只原样透传，不在投影层解释或授权。
3. **结构区分，不猜标签**：一条 affordance 是否"在尝试做可执行停留定义"只看是否显式写了
   `execution`。没有 `execution` 的未知 ID（MOVE、橱窗、长椅……）是普通环境描述：零停留
   候选、不报编译错误。显式 `execution` 不成立才报固定错误码。同一未知 ID 的两种 fixture
   因此可测：有无 `execution` 决定它是环境还是坏定义，而不是 ID 像不像动作。
4. **legacy 与报错**：已有完整 legacy 绑定（在 `STAY_DESCRIPTOR_PREFERENCE` 与
   `STAY_SECONDS_BY_AFFORDANCE` 两张表里）且没写 `execution` 的 affordance，继续按原准入、
   排序与秒数工作；其它旧活动不删除、不迁移。`REST`/`READ_HERE` 的显式 title/秒数覆盖规则
   保持有效。mixed 合法 + 未知显式定义时整体报错，不悄悄滤掉坏定义；闭店/暂不可执行仍在
   第二层先拒绝，不是定义错误。
5. **删除静默兜底，错误从正常入口抛出**：`_stay_descriptors` 不再自动补 `QUIET_STOP`。
   没有可执行停留下定义时返回零候选；显式定义坏掉时抛 `StayDefinitionError`，其
   `code` 是闭集固定码、`path` 是安全结构定位。正式入口——`loader.load`（落盘世界包
   校验）、catalog 读取（坏包返回 unavailable，不静默成 available/空成功）、
   `WorldMoveOpportunityProvider.observe()`——遇坏定义都向调用方暴露它：provider 不再
   捕获成空列表，异常沿 Life cycle 既有的 `last_world_error` 通道被实际消费。
   `last_stay_definition_status` 仍作为同轮的定义层诊断，但不是唯一通道。
   加载时 `WorldPackageError.code/path` 沿 `WorldDataError` → catalog unavailable
   envelope → `read_location_catalog` 一路保留：后者对 code 做闭集校验、对 path 做
   形状校验后才透出，`WorldMoveOpportunityProvider` 用真实默认 reader 读到同一条
   定义错误时向调用方抛同名 `StayDefinitionError`；文件缺失/JSON 坏等普通读失败
   仍只有 `WORLD_PACKAGE_UNREADABLE`，不带 code/path。
6. **错误带安全结构定位**：`WorldPackageError` 增加可选 `code`/`path`；affordance 定义
   错误的路径形如 `rules.json/location_rules/<合法location-id>/affordances/<index>/<合同字段>`。
   `location_id` 沿用 `schema.ID_RE` 与 `MAX_LOCATION_ID_LENGTH`（Life 侧遇到不合法 key
   用 `*` 代替并不回显），`index` 是严格整数，字段只取 `affordance_id/execution/title/
   suggested_seconds` 闭集；不回显 description/title 值、异常正文或主机绝对路径。
7. **两层独立，次序修正**：正式 load 不看地点是否闭店，坏定义一律拒；provider 在收到
   未校验 catalog 时先做第一层（定义）再判第二层（营业），坏定义即使 CLOSED 也报定义
   错误，合法定义闭店仍按第二层返回 `closed` 且零候选。

## Alternatives considered

- **建一张全域活动注册表，把所有世界 affordance 都注册成动作。** 最强立论：一次注册就能
  消除 Life 侧白名单维护，未来加活动不用改代码。否决原因：那会把"世界能描述"偷换成
  "世界已授权执行"，改变 World→Life 的准入合同，也超出本人这次只做 REST/READ_HERE 第一层
  的范围。
- **把所有未知旧 ID 自动当错误或自动当可执行动作。** 最强立论：规则简单，不再需要区分
  环境与动作。否决原因：世界包里大量 affordance 本来只是环境/物件描述（MOVE、橱窗、长椅），
  全报错会让普通世界包失效；全当动作则等于绕过准入与授权。本单按可检查的结构信号
  `execution` 区分，既不猜自由文本，也不给旧 ID 自动放权。
- **保留 `QUIET_STOP` 作为缺定义时的兜底。** 最强立论：任何她真实所在的地方都能"待一会儿"，
  不会因为世界包漏写而卡住。否决原因：本人明确不要"漏写静默变成待一会儿"，这会把配置
  错误伪装成她的选择；停止条件是报错，而不是替她选一个兜底动作。
- **从 `description` 解析时长或机制。** 最强立论：零 schema 改动，作者只写一句话即可。
  否决原因：自由文本不可验证，会把展示文本变成计划来源，也正是本次禁止的做法。

## Consequences

- 已知行为变化（有意）：地点只有未知/未绑定 affordance 时，不再出现 `QUIET_STOP`「待一会儿」
  候选；改由 `last_stay_definition_status` 报告 `environment_only`。显式写坏的 `execution`
  在正式 load / catalog / provider 入口都报固定 code + 安全 path，且不产生候选；
  provider 不再把定义错误吞成空列表。闭店合法定义仍零候选、状态 `closed`。
- 旧世界包与旧模板不需要迁移：没有 `execution` 的完整 legacy 绑定行为不变；本单只在
  `coastal_town` 的 `home` REST 与 `bookstore` READ_HERE 各加一个最小显式示例。
- 第一层通过不代表候选合法或执行授权：`action_grant` 仍为 false，AG-0/Decision 与第二层
  营业/权限判断都没有新增入口。
- 验收：`tests/test_strict_activity_definition.py` 4 个聚合用例覆盖正式生成→schema/load/
  catalog→现有候选→adapter/client/script transport 实收 JSON（标题/秒数/机制、NO_ACTION）、
  正式 `loader.load` 坏定义负控（code+path、缺 title / 未知 mechanism 两类）、
  同两个坏包走真实 `read_location_catalog` 保留 code/path 且不回显标题/主机路径、
  同一坏包经 `WorldMoveOpportunityProvider` 默认 reader 向调用方抛同名定义错误、
  正式 `WorldMoveOpportunityProvider.observe()` 负控（code+path、闭店不短路、混合不滤掉、
  非法 location key 不回显）、provider 正控（合法 REST 可读、环境描述 environment_only、
  合法闭店零候选）、legacy 与三层边界；`tests/test_affordance_optional_metadata.py`
  4 个原用例保留。
- 未验证：真实模型是否会因为机制信息改变选择、浏览器呈现、以及"世界包作者是否总是写
  execution"都不在本单范围。第二/三层是否拒绝某个具体候选仍需各自的既有测试与真实运行。
- 相关既有决定仍然有效，本笔记只替换其中"未知→安静兜底"这一点：
  [affordance-optional-metadata](2026-11-07-affordance-optional-metadata.md)、
  [idle-and-dawdle](2026-11-05-idle-and-dawdle.md)。
