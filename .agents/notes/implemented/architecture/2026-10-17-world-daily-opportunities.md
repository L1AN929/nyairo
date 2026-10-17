# Agent Note: 有限街区场景与只读机会目录（World 侧）

Status: implemented

## Problem

生活基础跑通后，“出门”这一步只有两样东西：一份把汐见市六地点写死在代码里的活动世界，和靠参数传入的观察。真实问题是三处缺口：

1. 没有一个可以真的走、真的回家、真的遇到关门的**有限街区静态数据包**：既有 `examples/worlds/*` 都是整镇生成物（61KB 场景、十几个地点），拿来做“今天下午去哪儿”的验收既太大又不可控。
2. 心智侧看不到“此刻能去哪儿、那儿开没开门、走过去要多久、哪些事实是公开时间表而不是亲眼看见”。要么全城广播（越界），要么什么都不知道（只能待在家）。
3. 原有 `plan_move_to/apply_move_to` 在未开计时旅程时，对 CLOSED 的地点直接返回 `rejected/LOCATION_CLOSED`——把营业条件写进了**出发**这件事里。结果是“店关门了就连门口都走不到”，与“出发/到达是物理事实、营业约束的是到场后的动作与消费”这个既定分层相反。

## Decision

### 场景包：`examples/worlds/daily_neighborhood_v1/`

仓库既有的世界包标准位置是 `examples/worlds/<world_id>/`（七个随仓提交的包都在这里，`components/worldgen/tests/test_edited_world_runtime.py` 直接引用其中之一），因此包落在 `examples/worlds/daily_neighborhood_v1/`，而不是派工里写的 `components/world/packages/`（该目录在仓库中不存在）。

包内容（`worldgen.package.v1`，用仓库自己的 `worldgen.schema.write_package` 写出并带 MANIFEST）：

- 六个地点：`home`(home/bedroom, 室内区域卧室+起居室)、`street_front`、`street_corner`、`park`、`cafe`(室内 main+counter)、`reading_point`(library，室内 reading_room+shelf)。`validator` 要求 ≥6 个地点，故街区保留两段街道。
- 五条边，全为真实正秒：`home–street_front 90`、`street_front–street_corner 120`、`street_front–reading_point 150`、`street_corner–park 180`、`street_corner–cafe 120`。从家出发：公园 390s、咖啡店 330s、阅读角 240s。
- `rules.location_rules`：公园 6–22、咖啡店工作日 8–18（`weekdays_only: true`）、阅读角 10–20、家/街道常开；affordance 里保留一项**不需产出**的停留（公园 `SIT`、阅读角 `READ_HERE`），咖啡店给 `BUY_DRINK` 但消费仍归饮食 owner（本包不声明库存/支付）。
- `scene.json` 用 `navigation` 同时声明室内区域（卧室的桌与床、咖啡店的桌子与柜台、阅读角的阅读桌与书架）和户外地点区域（人行道/路口/环园步道），因为运行时的可见物件只来自 `navigation.furniture_map` + `placements`；`objects.json` 仍只作对象目录，不当作可见家具来源。
- `npcs.json` 为空、`ambient.json` 只有两条环境模板：不造虚构 NPC 经济，也不预写剧情。

### 只读机会目录：`components/world/scripts/world_daily_opportunities.py`

`location_opportunity_catalog(environment=None, now=None, *, hermes_home=None)` 只做投影，事实各有来源：

- 可达性来自 `world_runtime_data.active_world()` 的走道图：`route_to` + 逐段 `route_duration_seconds` 求和，单程上限 `MAX_WALK_SECONDS = 1800`；当前位置以 `status: "here"`、秒数 0 列出。
- 可用性/可做事项来自 `world_daily_rhythm.location_availability` 与 `affordances_for_location`：本模块不自造营业判断，闭店时 `affordances` 恒为空。读取失败时该地点报 `status: "UNKNOWN"`, `reason_code: "DAILY_RHYTHM_UNAVAILABLE"`，不补一个“可能开着”。
- 可见物件只对“此刻真的在这里”的地点投影（`observed.status` 为 `visible_here`，`source` 为 `world_package_scene`）；别处一律 `not_observed` + `OUT_OF_VIEW`，远处营业只作时间表事实，`schedule_source` 标明 `world_package_schedule`（包世界）或 `world_daily_rhythm_builtin`（汐见市）。
- 每条目带 `validity: {as_of, scope: "NOW_ONLY", future: "not_promised"}` 与 `action_grant: false`：只知道本次 `now` 的事实，投影不是动作权限。
- 不可读/未初始化/位置非法返回 `status: "unavailable"` 与 `WORLD_PACKAGE_UNREADABLE` / `WORLD_NOT_INITIALIZED` / `WORLD_LOCATION_INVALID`，`opportunities` 为空。

`compact_projection(catalog)` 是给 `observe_world` 的短视图；完整目录仍由调用方直接取。

### `observe_world` 兼容扩展

`world_living_skeleton.observe_world` 在结果里新增 `reachable_opportunities`（compact），其余既有字段不动；若加上后超过 `MAX_OBSERVATION_BYTES`（16KB），只省略这一个字段，不退化成 `PROJECTION_BOUNDS_EXCEEDED`。`read_world_state()` 作为唯一“当前世界状态”只读口公开出来，供投影复用，避免第二份解析。

### MOVE 与营业解耦

`plan_move_to/apply_move_to` 删除 `LOCATION_CLOSED` 与 `DAILY_RHYTHM_UNAVAILABLE` 两个拒绝分支：到达门口是物理事实，闭店只影响到场后的 `affordances` 与消费机会。营业事实改为结果里的 `destination_availability` 提示字段（未知时为 `null`），不改计时旅程、不改已出发路段、不强制目的地。

## Alternatives considered

- **把街区包放进 `components/world/packages/`** — 派工给的字面路径更贴近“World 组件自带数据”的心智模型，找起来也顺；但仓库里没有任何包住在那，所有随仓包的加载/校验/预览/实例化入口都按 `examples/worlds/<id>/` 走，另开一棵树等于让这个包成为唯一不走标准加载路径的世界。否决，改在标准位置并用专属 `daily_neighborhood_v1` 目录名。
- **机会目录另建一份营业/库存事实** — 心智侧要的字段比 `world_daily_rhythm` 现有形状更丰富（可达性、可见物件、有效期一起给），自建一层最省对接；但那等于第二个营业真相，`open_hour/weekdays_only` 一改就会两边打架。否决，只做投影，缺什么就从既有 owner 读。
- **把机会目录做成动作授权（带 `allowed: true`）** — 下游接线一步到位，不必再问 Resolver；但授权是 Resolver/owner 的判断，投影一旦“批准”就等于绕过动作真实性与台账。否决，全部条目 `action_grant: false`，只给事实与来源。
- **保留 CLOSED 就拒绝 MOVE 的旧行为** — 它确实防止“走到关门的店门口白跑一趟”，是很强的理由；但它把营业写进了出发条件，和“出发/到达是物理事实、关门约束的是店内动作与消费”冲突，也会让“等店开门/门口折返/临时改主意”这些真实日常无法表达。否决。
- **给户外地点也声明 `navigation`** — 严格说 `navigation` 的注释写的是“建筑内部区域”，给街道/公园用略有扩张；但不给的话，街道与公园的可见物件在运行时没有任何暴露通道（`objects.json` 不参与可见家具投影），场景就只剩空壳。保留，并在数据与笔记里写明它承担的是“可见区域/摆位声明”。

## Consequences

- **收益**：出门这件事第一次有可复现的有限场景：六个地点、真实正秒路段、可回家；`location_opportunity_catalog` 的每条事实都能追到 owner 与来源字段，Life 侧接线不需要再自造规则。
- **收益**：营业条件从“出发拒绝”变成“到场事实”，闭店可到门口、可折返、可等开门，验收里“闭店没有店内 affordance”与“仍能规划到门口”同时成立。
- **代价与已知上限**：`scene.navigation` 被复用来暴露户外可见物件，是既有 schema 的扩张使用，不是新契约；一旦未来给户外单开声明方式，这份包要跟着改。`MAX_OPPORTUNITIES`/`MAX_WALK_SECONDS` 是常量而非配置，街区做大时必须重访。`observe_world` 的短视图有 16KB 上限，超限时只省略机会字段——调用方要拿全量就必须直接调 `location_opportunity_catalog`。
- **代价与已知上限**：`components/worldgen/tests/test_edited_world_runtime.py::test_edited_opening_hours_actually_govern` 原断言 `rejected/LOCATION_CLOSED`，与本次决定正面冲突。**收口更新（2026-10-17，父级裁定不回退，授权独占改这条测试）**：该断言改为“书店与家不相邻 → `LOCATION_NOT_REACHABLE`，且显式不等于 `LOCATION_CLOSED`”，11/13/20 三条营业 owner 断言原样保留；另加独立用例 `test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty` 用真实相邻闭店对 `park↔bookstore` 证明：11 点 plan/apply 到门口均成功并带 `destination_availability = CLOSED/OUTSIDE_OPEN_HOURS`、`affordances_for_location == []`，13 点开门才出现 `BROWSE_STORE`。产品代码未因此修改。

## Verification

- `tests/test_world_daily_opportunities.py`（10 用例，WSL `w04venv/bin/python -m unittest`）：包校验/加载、`init_instance` 真实建实例、observe 真实机会与竞争、8:00/18:00/周末/跨日边界、闭店到门口与按路段回家、计时旅程闭店仍可出发、未初始化/包不可读/营业规则不可读的原因码、观察零写入、走道真实秒数。
- 同批回归：`tests.test_world_move_tool`、`tests.test_world_move_tool_navigation`、`components.worldgen.tests.test_v04_navigation`、`components.worldgen.tests.test_runtime_wiring`（50 用例）全绿；`tests.test_world_journey` 全绿。
- 闭店门口语义的验收落在 `components/worldgen/tests/test_edited_world_runtime.py`：`test_edited_opening_hours_actually_govern` 保留 11/13/20 营业断言并改为可达性拒绝，新用例 `test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty` 用真实 `park↔bookstore` 验证门口可走、店内 affordance 为空；该模块 5 用例 exit 0，与 W 关联模块合跑 65 用例 exit 0。
- 实例初始化命令：`python -m components.worldgen instance --package examples/worlds/daily_neighborhood_v1 --home <隔离实例目录>`；运行时用 `CHIYO_WORLD_PACKAGE=<那个包>` 指向它。
