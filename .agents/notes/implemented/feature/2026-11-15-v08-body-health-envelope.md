# v0.8 · S02 健康状态机、容量信封与真实决策链

## Problem

`body_runtime`（`body.runtime.v2`）只描述“累不累”，没有病程、没有“无信号不等于健康”的可执行判据，也没有 S02→S04 的收窄契约。生死、疲劳和创作阻断不能只靠提示词表达；同时 v03 已经用 `v03_ecology.json` 的 `illness` 管着淋雨/熬夜的多日曲线，不能再造第二套病程。

## Decision

- `components/world/scripts/body_health.py`：`HealthStatus` 五档；双滞回（急性进入 `s ≥ 0.35`、退出 `< 0.25`）；严重度按 `ds/dt = α·u(t)(1-s) - γ·s` 闭式推进，无暴露时严格递减；康复需 `s < 0.08`、离开急性期 ≥ 18h、慢性疲劳回基线；来源白名单 `carrier_selfcheck` / `world_exposure` / `governance_maintenance`，`conversation` 永远拒绝；`derive_envelope` 产出容量指数、候选上限、创作阻断、二次核对、计划步数、并发与延迟下限。容量只有 `LIMITED` / `UNKNOWN`，病不是锁。
- `components/world/scripts/body_health_projection.py`：把真实 runtime + 持久 `health.json` + v03 病程接成一份投影。急性疲劳以 runtime 为准；`health.json` 用同目录临时文件 + `os.replace` 原子落盘，读取严格，损坏即拒、不重置。
  - `record_from_illness` 的身份与时间取 v03 自己的 `episode_id` / `episode_opened_at`（回退 `acute_entered_at` / `phase_entered_at`），重复 GET 不漂；缺来源时间返回 `UNATTESTED` + `ILLNESS_EPISODE_UNSOURCED`，不拿观察时刻冒发病时刻。
  - `status_authority="v03_illness"` 让 `prodrome` 保持 `STRAINED`，不被通用滞回改写成 `CONVALESCENT`；`acute_illness` / `convalescence` 按各自真实阶段走。
  - 没有当前 runtime 体感信号时不报 `HEALTHY`：旧 `health.json` 的“健康”不能比证据活得久；缺 runtime 为具名 `UNATTESTED`。
- `world_body_service.py`：`health_record()` 是 v03 病程与持久记录的唯一合流点；`_advance_due` 在身体真正推进后推进病程。`_persist_consequence` 只在真实物理结算给出 `load_units > 0` 时把同一区间的负荷喂给慢性负荷/睡眠债——空闲与离线补时不计债，也不把固定 0 说成“已测无债”。
- `world_body_gateway_api.py`：`get_body_health` 与 `get_body_signals` 共用同一投影；后者在投影异常时输出具名 `UNATTESTED`/`UNKNOWN`，而不是省略 `health` 或当健康处理。HTTP 只给定性档位与契约允许的容量信封，没有原始 somatic 浮点。`get_body_signals` 的 `health` 块还带 owner 自己的 `body_id` / `body_version` / `observed_at`：真实链上 runtime 可能一个负担信号都没越过闩锁，健康事实不能因为没有信号就丢掉身份与时刻。
- `body_capacity_source.py`：病程（`STRAINED` / `ACUTE_ILLNESS` / `CONVALESCENT`）本身可以作为容量证据，即使 `signals` 为空；这时视图绑定 `health` 块的身份/版本/时刻，仍走既有的身份、修订、时效检查。安静健康（`UNATTESTED`/`HEALTHY`）保持 `UNKNOWN`，不会因为信封存在就被说成受限。调用方在读取 owner 之前捕获轮次时刻，观测发生在读取之中，因此同轮内的微小“观测晚于参考时刻”按读取延迟处理，不再误报时钟回拨；既有的多十分钟/小时级 AHEAD 拒绝不变。
- `phone/service.py` + `server.py`：`GET /api/phone/body/health` 零写入，读同一 `runtime.json` / `health.json` / v03 文件；缺 runtime 或记录损坏时不报健康。旧的已发布字段保留，未发布的裸 `episode.severity` 已替换为 `severity_band`（见 `docs/engineering/v08-body-health-api.md`）。
- `agency_decision_ag1.py`：先按候选的结构化身份拒绝高负担创作，再对剩余合法低负担候选按原顺序施加 `body_candidate_cap`；`apply_candidate_cap` 只切片，不排序、不打分、不抽签。创作身份判定的反转单列在 [[2026-11-15-v08-health-identity-gate]]。
- 消息链：`health` → `body_capacity_source`（`body_candidate_cap` / `body_max_plan_steps` / `body_creative_block`）→ `life_situation_view` body 列 → `AutonomousLifeCycle.capacity_kwargs` → 真实 `AgencyDecisionService.evaluate_opportunity`。`life_integration_ag2.cap_step_spec` 在解出多步规范时按 `max_plan_steps` 截断并标记。

## Alternatives considered

1. **直接扩展 `body_runtime` 的状态结构**。最强理由：状态只有一个来源，不需要第二个文件，重启天然一致。否决原因：`body_runtime` 的契约是“体感量 + 恢复状态机”，病程有独立生命周期（来源、峰值、离开急性期、关闭时间），混进去会让 v2 变成大杂烩，也会迫使所有读 v2 的既有代码理解病程。
2. **在决策入口直接读 `body_health` 并裁剪候选集**。最强理由：这才是“硬压制”的最终形态。否决原因：这等于同时改 S04 准入契约；先把信封与只读面做对，再通过既有的 `body_candidate_cap` 参数接线，影响面最小。
3. **病程存在世界里（World 侧事件）**。最强理由：与慢变量、因果边共用一个账本。否决原因：设计第一章已裁定 S02 拥有载体内部状态，世界侧不持有身体事实，写进世界快照会污染世界哈希。
4. **再建一套病程镜像账本**。最强理由：投影可以完全自洽，不依赖 v03。否决原因：v03 已经是真实多日曲线的 owner；第二套账本必然分叉，且 GET 不能为了读而写镜像。

## Consequences

- 收益：健康档位、病程、容量信封与候选裁剪现在是同一条真实链；`UNATTESTED` 有硬判据，缺证不会被升级成健康或无限容量；低负担的阅读/休息/喝水不会被创作阻断误伤。
- 边界（2026-11-15 具名修正）：创作拒绝在候选上限**之前**执行，但上限本身只是对候选列表取前缀，而 AG-1 拿到的顺序是 AG-0 为序列化稳定而做的 `candidate_id` 排序（`candidate_id` 是 `source_kind + artifact_uuid + revision` 的哈希）。因此当同轮合法低负担候选多于 4 个时，被切掉的是哈希序末尾的选项，阅读也可能被切掉；这不是固定优先级，也不是阅读专属保护。真实链测试为此把 v02/v03 日循环机会源（病程照护/发呆提示，另一个源）在**该测试轮**显式关闭，使它自己的 4 个真实候选（绑定作品 continue + closeout、阅读材料、一个 World 观察）正好填满 cap 4；候选上限与顺序本身没有改动。
- 证据：`tests/test_body_health.py`、`test_body_health_projection.py`、`test_body_health_gateway.py`、`test_health_cap_wiring.py` 聚焦覆盖状态机、缺证、损坏、身份稳定与裁剪顺序。`tests/test_v08_health_decision_chain.py` 分两层：`HealthReadChainTest` / `DecisionAdoptionChainTest` 是组件层（真实 WorldBodyService 方法绑到 shim、真实 gateway socket、真实 AG-1/AG-2，脚本候选与认知替身）；`RealOwnerReadingChainTest` 是**真实 owner 执行链**——`one_day_life.prepare` 新实例、真实 Supply owner、真实 `WorldBodyService` 网关 socket、`apply_v03_ecology_command` 正规写入两次 `caught_in_rain`（`prodrome` → `acute_illness`，episode `ep_...`）、真实 `read_life_situation` 与 `cycle.capacity_kwargs`（`LIMITED` / cap 4 / `creative_block=true` / `max_plan_steps=1`）、真实 AG-1 Decision、真实 AG-2 Adoption、真实 AR-0 阅读步骤（第 1 页、私人物品文档 head 前进、作品 head 不变）。认知只在这条链里是脚本替身；脚本按 artifact 身份（剥掉重复 `artifact:` 前缀）匹配，并先断言 AG-1 真的把阅读候选放到了面前。该测试轮显式关闭 v02/v03 日循环机会源，理由见上一条边界。
- 代价：`health.json` 是一个新的持久文件；慢性负荷/睡眠债依赖 Body 的实测 `load_units`。该值是 `body_runtime._load_units` 算出的**小时当量**（移动小时 × effort 系数 + 姿势小时 × pose 应变），不是无量纲 tick 数，也不是每 tick 的总值；`_persist_consequence` 按 `load_hours` 原样交给 `advance_record`。同一 `execution_id` 的重放由既有 `body_consequence.apply_for_execution` 判为 `duplicate_execution_id` 且不产生 `advance`，因此同一执行只计一次；没有物理结算的实例保持 0（未累积），不会被当成“无债”。
- 边界：不改 Body schema、不改既有动作许可/默认 gate；不新增写病程 HTTP API；桌面端与已安装 exe 不在本单内。
