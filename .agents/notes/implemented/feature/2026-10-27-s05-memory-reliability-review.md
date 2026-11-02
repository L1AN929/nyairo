# Agent Note: S05 有来源经历、交接/准入分离与 Memory 分层读面

Status: implemented

## Problem

S05（F-066～F-071）要求把「真实结果 → 交接 → 准入 → Memory 记录」这条链看清楚，
但现有实现分散在 AG-2 的 handoff store、consumer 的投递记录和 M0 的治理读口里，
没有任何一个读面能把它们分开显示。直接把这些东西合成一句话，就会出现三种实际错误：

1. **把交接成功说成「记住了」。** AG-2 已经签发 handoff、投递记录写着 `ACCEPT`，
   但治理 DELETE 生效后这条记录当前不可召回；两者不是同一件事。
2. **把 `DUPLICATE` 说成完整评价。** 同一结果重投时 M0 自己的幂等准入返回
   `duplicate`（零新行），这是真实的存储级准入结果；它不是设计 Q30 的语义
   `DO_NOT_ADMIT` 评估，也不能拿 writer 的 `no_change` 冒充。
3. **造空表冒充派生层。** 设计 Q27/Q28 的 Episode 分组、episode summary、
   embedding/检索索引在当前实现里不存在；给一个空列表会被读成「已实现但没数据」。

## Decision

### F 条目实际到哪（不把下游能力说成已交）

- **F-066 有来源 Episode**：真实 owner 结果 → handoff → 一条带来源 refs 的 M0 Life
  experience。Episode 分组未实现，只证 M0 source 可证，**不称 Episode 完成**。
- **F-067 交接与接纳分离**：handoff 身份 / 投递 receipt / M0 当前召回三项分开投影。
- **F-068 共同根事件去重**：同结果重投 `DUPLICATE` 零新行；异版本各自成记录不误吞。
  仅此现有存储层。
- **F-069 raw 与 summary 分层**：原始来源与 M0 记录可读；`episode_summary` 不存在，
  以 `NOT_IMPLEMENTED` 具名。
- **F-070 派生 embedding/index 失效**：`embedding_index` / `derived_invalidation`
  都不存在，`NOT_IMPLEMENTED`。
- **F-071 语义 NO_CHANGE 评估**：只有 M0 幂等准入的 `DUPLICATE`；Q30 语义评估器
  不存在，`NOT_IMPLEMENTED`。

重启是额外验证，不属于 F-071。`DUPLICATE` 不是语义 `DO_NOT_ADMIT`。

### 读面

新增 `components/life/scripts/life_memory_review.py`（纯读）与
`scripts/s05_memory_review_cli.py`：

1. 读 AG-2 既有的 `handoff_events_by_id` / `handoff_deliveries_by_id`，给出**源事件**
   （handoff id、命名域、动作/结算/作品/版本 refs）和**历史准入 receipt**
   （`status` / `consumer_disposition` / `attempt`）。不重新计算 handoff 身份。
2. Memory 一侧只走 `LifeMemoryService.list_experiences`（即现有
   `LifeExperiencePort.search` + 治理投影），按 `source_ref == handoff_id` 精确匹配；
   `READY` 但零行时报 `NOT_RECALLABLE`，不是「从未交接」。
3. 分层状态：`source_evidence` / `accepted_memory_record` 为 `AVAILABLE`；
   `episode_grouping` / `episode_summary` / `embedding_index` / `derived_invalidation`
   一律 `NOT_IMPLEMENTED`。`read_gate` 指出现有治理投影在 search/read/context 上生效；
   读端实际抛错时，服务返回具名 `UNAVAILABLE`，不变成空表，也不冒充「当前可召回」。
4. `no_admission_view`：`DUPLICATE` 只报
   `NO_CHANGE_AT_M0_IDEMPOTENT_ADMISSION`（零新记录）；语义
   `semantic_do_not_admit` 报 `NOT_IMPLEMENTED`。两者不混。
5. 读面不输出正文/描述/prompt/reasoning；owner_confirmed 只给 identity 和
   `description_present`/`title_present` 布尔值。

**没有新增** Episode/summary/embedding 持久格式、Memory 写/准入合同、删除权威或
新评估器。既有治理的 epoch 行为也没有改：一旦存在控制记录，控制之前的 `chiyo`
自述历史整体退出召回（`MemoryProjection` 现状），所以它本来就不是逐源隔离。

治理 DELETE / CORRECT 的扣留链在本轮 demo 里 **NOT_RUN**：DELETE 不被本任务允许；
CORRECT 需要一条直接用户输入（`speaker='user'`、`USER_VISIBLE_INPUT`）事件，
隔离演示没有这个真实来源。demo 只做一次可撤的读端故障注入来验「读失败不冒充
可召回」，撤除后恢复读取；这不是撤权，也不是治理扣留。

## Alternatives considered

- **新增 Episode/summary 存储以补全分层**：最强理由是设计 Q27/Q28 明确要求分组与
  带 member revision 的派生记录。否决原因：需要新持久格式、依赖边协议和失效传播，
  属于本单明确禁止的合同变更；只读层先如实报 `NOT_IMPLEMENTED`。
- **把 `DUPLICATE` 直接当 `DO_NOT_ADMIT`**：最强理由是 M0 已经做了去重，结果都是
  「没有新记忆」。否决原因：去重只看 `(source_origin, primary_source_ref_id)`，
  不评价重要性/类别/保留权；把它说成完整评价会掩盖语义准入尚未实现这一事实。
- **读面直连 SQLite 查 handoff 与 M0**：实现最直接。否决原因：M0 读路径已有治理
  投影和具名失败形状，直连会写出第二份治理算法；本模块只复用既有 port/service。

## Consequences

- 用户/后续会话能分别看到「交接发生过」「当时准入结果」「现在还能不能召回」，
  不会把三者压成一句。
- 当前功能上限被明确写出来：没有 Episode/summary/embedding，没有语义
  `DO_NOT_ADMIT`；这些不是本轮遗漏，而是需要新合同的下一层。
- 既有 epoch 规则会让控制之后的旧自述历史整体不可召回；本单不改变它，读面也不
  把它描述成逐条隔离。该规则本轮没有在 demo 里触发（未下任何控制）。
- 只证隔离链：真实 Supply/M0/控制账本/AG-1/AG-2/正式 `evaluate_once`；
  Decision 与内容是脚本替身。不证真实模型自然采用、G1、24h 或跨 PID/crash。

## Verification

- `tests/test_s05_memory_review.py`：6 例，覆盖身份/准入/召回分离、不可召回形状、
  派生层具名、`DUPLICATE` 不等于语义评价、正文不泄漏。只读模块形状，不跑真实 M0。
- `scripts/s05_memory_reliability_demo.py`：真实结果 handoff → ACCEPT；第二轮另一
  确证版本独立成第二条；同结果重投 `DUPLICATE` 零新行；可撤读端故障下
  search/read/context 全部具名 `UNAVAILABLE` 且不增行，撤除后恢复读取；正规停启后
  同一 handoff/投递/Memory 记录读回，零新 Action/版本/记录。
- 未运行：治理 DELETE/CORRECT 扣留链（见上 NOT_RUN）、物理删除、旧备份恢复、
  跨 PID/crash；真实模型自然采用、G1、24h 均未证。0 产品模型、0 外网。
- 相关既有决定：
  [Memory 产品化接缝](./2026-10-27-v04-memory-product-service.md)、
  [进度交接的结果级身份](../architecture/2026-10-25-v05-progress-result-handoff-identity.md)。
