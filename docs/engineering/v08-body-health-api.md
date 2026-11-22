# v0.8 · 身体健康读面 API

本文只记录**已经存在**的 HTTP 读面及其真实来源；不包含尚未裁定阈值、写 API 或桌面端打包。

## `GET /api/phone/body/health`

零写入。返回手机端唯一的安全健康投影，不输出原始 somatic 值或严重度浮点。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 固定 `phone.body_health.v1` |
| `ok` | 读面是否可用 |
| `health_status` | `HEALTHY` / `STRAINED` / `ACUTE_ILLNESS` / `CONVALESCENT` / `UNATTESTED` |
| `attested` | 当前是否有真实身体证据；缺 runtime 时为 `false` |
| `since` | 该状态或病程的来源时刻（来自 v03 文件，不是本次读取时间） |
| `episode` | `null` 或 `{open, origin, severity_band}`；`severity_band` 只有 `mild/noticeable/strong` |
| `capacity` | `{index, candidate_cap, max_plan_steps, creative_block, latency_floor_ms, state}`；`state` 只有 `LIMITED/UNKNOWN` |
| `health_source` | `v03_illness` / `health_record` / `none` |
| `reason_code` | 具名缺口，例如 `RUNTIME_UNAVAILABLE` / `RUNTIME_UNREADABLE` / `HEALTH_RECORD_UNREADABLE` / `ILLNESS_EPISODE_UNSOURCED`；正常时为 `null` |
| `energy` | 手机侧从同一 `runtime.json` 读出的疲劳档与大致精力区间；runtime 缺失时为 `null` |
| `read_only` / `writes_performed` | 常量：`true` / `0` |

## 来源与优先级

1. **v03 真实病程**（`<day_root>/phone/v03_ecology.json` 的 `illness`）：`prodrome` → `STRAINED`，`acute_illness` → `ACUTE_ILLNESS`，`convalescence` → `CONVALESCENT`。身份与时间取 `episode_id` / `episode_opened_at`（回退 `acute_entered_at` / `phase_entered_at`），重复 GET 不漂移。
2. **持久健康记录**（`<world_home>/data/body/health.json`）：慢性负荷、睡眠债、已落盘的病程。
3. **真实身体运行时**（`<world_home>/data/body/runtime.json`）：急性疲劳与当前身体证据。

同一个投影同时供网关 `get_body_health` / `get_body_signals` 和手机读面使用，因此两处不会各说一套。`get_body_signals` 的 `health` 块额外带 owner 自己的 `body_id` / `body_version` / `observed_at`，供 `body_capacity_source` 在 runtime 没有任何负担信号时仍把病程绑定到真实身份、修订和观测时刻。安静健康（`UNATTESTED` / `HEALTHY`）不会因此变成 `LIMITED`。

## 急性病中的候选策略

`creative_block=true` 时，AG-1 先在硬约束之后、cap 之前执行创作门：真实作品续写（`artifact:artifact:<work>`）、收口（`closeout:artifact:...`）、手稿稿费（`v03_job:manuscript_fee`）、创作类 `popt:` 项目项，以及正在写作品的 `CONTINUE_CURRENT`，都以 `HARD_REJECT:BODY_CREATIVE_BLOCK` 拒绝；阅读延续（`reading_artifact_id` / `reading:reading_ar0:`）、`v03_care:bed_rest_block`、`v03_care:warm_hydration`、`v03_idle:daydream_window` 与日常低负担行为不受影响。识别依据是生产者在候选 `provenance_chain` 里写的 `step="signal"` 轻量标签（`domain` / `creative_intensive` / `source_ref` / `action_family`），标签在候选 id 与 `semcand:` 身份算完之后才追加，不改变任何去重哈希；旧候选仍走 `candidate_kind` 与字面语义兜底。

被创作门拒绝的候选不占 cap 席位。`apply_candidate_cap` 在健康收窄激活时（创作门为真、`LIMITED` / `FATIGUED` / `ACUTE_ILLNESS` / `RECOVERING`，或 cap ≤ 6）按语义分席：先保留最多 1 项真实身体休息（`v03_care:bed_rest_block` / `REST`，`daydream_window` 不算休息）、最多 1 项温水补水、最多 1 项当前在读材料，其余席位按确定性语义序补齐（低负担照护/日常 > `NO_ACTION` > 发呆）。任意 `cand:<sha256>` 不参与排序；同一组真实角色在不同随机 UUID 和不同输入排列下得到同一结果。非病中路径保持原有顺序截断。

## 真实 owner 执行链（测试证据）

`tests/test_v08_health_decision_chain.py::RealOwnerReadingChainTest` 在同一个新实例、**全部真实候选源开启**的条件下验证：`apply_v03_ecology_command` 的两次 `caught_in_rain` 把 v03 病程从 `prodrome` 推到 `acute_illness`；真实 `WorldBodyService` 网关读回 `ACUTE_ILLNESS` / `LIMITED` / cap 4 / `creative_block=true` / `max_plan_steps=1`；真实 `read_life_situation` 与 `AutonomousLifeCycle.capacity_kwargs` 把这些值交给真实 AG-1/AG-2。病中一轮的创作拒绝 ≥ 2（作品续写与收口），cap 真的裁掉 ≥ 1 项，进入认知请求的 4 项同时包含卧床休息、温水与阅读材料；AR-0 阅读步骤真的读第 1 页，Supply owner 落下新的私人物品版本（书签前进），作品 head 不变。随后同一实根通过 `v02_v03_daily_loop_bridge.settle_v02_v03_round_consequences` 的真实 `REST` / `WARM_HYDRATION` / `ADVANCE_CLOCK` 事件把病程恢复为 `HEALTHY`（`creative_block=false`、`candidate_cap=null`），作品续写重新进入候选并被采纳，作品版本从 1 推进到 2。只有认知回答是脚本替身（0 产品模型、0 网络）。

## 慢性负荷单位

`body_runtime` 的 `inputs_summary.load_units` 是小时当量：移动小时 × effort 系数 + 姿势小时 × pose 应变。`_persist_consequence` 把它作为 `load_hours` 传给健康投影，不是把每 tick 的累计值反复入账。同一 `execution_id` 的第二份后果由 `body_consequence.apply_for_execution` 拒绝为 `duplicate_execution_id`，不会产生新的 `advance`，因此同一执行只计一次。

## 未知与缺失

- 没有 runtime、runtime 不可读或格式不对：`UNATTESTED` + 具名 `reason_code`，绝不报 `HEALTHY`。
- `health.json` 损坏：拒绝读取并返回具名 unknown，不重置成新身体、不建文件。
- v03 病程处于疾病阶段但缺少来源时间：`UNATTESTED` + `ILLNESS_EPISODE_UNSOURCED`，不把第一次观察当作发病时刻。
- `UNATTESTED`（尚无任何实证健康读数）在手机读面返回 `capacity.state = UNKNOWN`、`candidate_cap = null`、`creative_block = false`，`attested` 保持 `false`。它只是“没有证据”，不是“拒绝高强度创作”也不是“上限为零”；内部容量信封的保守常量仍供决策层使用，但读面不把未知翻译成拒绝。决策适配器只把 `LIMITED` 当负担证据，不会因未知而锁死。

## 边界

- 不新增写病程的 HTTP API；`GET` 不创建 `health.json`、不初始化 owner。
- 不改变既有动作许可、默认 gate 或 Body schema。
- 慢性负荷/睡眠债只在真实物理动作被 Body 结算且 runtime 给出实测 `load_units > 0` 时累积；空闲与离线补时不记债。若某实例没有真实的物理结算，就保持为 0 并视为“未累积”，不声称“已测无债”。
- 已发布的旧字段（`health_status` / `since` / `episode.open` / `episode.origin` / `capacity`）保留；未发布的裸 `episode.severity` 浮点已由 `severity_band` 取代。
