# Agent Note: 进度交接的结果级身份

Status: implemented

## Problem

AG-2 的 experience handoff 身份是
`activity_id + terminal_or_milestone_kind + Activity.revision`：终态（COMPLETED/ABANDONED）
连 revision 都不用，其它里程碑用 `lexp:<sha256(seed)[:16]>`。`CONTINUE` 在 Activity 上是
NOOP（不改状态、不改 revision），所以同一个 Activity 里的第二次真实作品写入会算出与第一次
完全相同的 handoff id；AG-2 走“已存在即重投旧事件”的分支，第二轮的版本引用、确证描述都
进不了事件，consumer 再按同一 `handoff_id` 判 `DUPLICATE`。结果粒度被 Activity 粒度吞掉。

2026-10-25 的只读核对确认这不是 cycle 漏传一个已有参数，也不是 M0 判错：cycle 已经把
owner 作品与版本引用放进 handoff 载荷；合并发生在 AG-2 `emit_experience_handoff` 的 idem
seed，consumer 忠实按 `source_ref` 去重。改它属于跨层身份契约，先停在 STOP 等裁定；本人
随后批准只为 owner 确证的 `SIGNIFICANT_PROGRESS` 增加显式结果级身份。

## Decision

1. `emit_experience_handoff` 新增可选参数 `progress_result_ref`。只有同时满足以下条件才可用：
   - `terminal_or_milestone_kind == SIGNIFICANT_PROGRESS`；
   - 带了有效的 `owner_confirmed_context`（`outcome_confirmed: true`，含
     `artifact_id` 与 `version_ref`）；
   - 参数值恰好等于 `canonical_owner_result_ref(owner_confirmed_context)`，即
     `artifact:<id>@artifactversion:<version>`；
   - 这两个 owner ref 都出现在本次调用携带的 `artifact_refs` 里。
   任何一条不满足都在写入前抛 `LifeIntegrationError`；裸字符串、时间、自由描述、模型自述、
   Activity revision 都不能当结果身份。
2. 新身份使用独立命名域 `lexp:result:<sha256(activity_id:kind:result:<ref>)[:16]>`，
   不与旧 `lexp:` 公式混用。同一确证结果重投时，即使 Activity revision 变了或重新装配，
   id 不变；同一 revision 的两个不同确证版本得到两个不同 id。
3. 缺新参数的旧调用完全走原公式与原行为；事件字段集合不新增，结果身份只体现在 id 与
   已存在的 `owner_confirmed_context` 上。终态 COMPLETED/ABANDONED 的旧身份原样保留，
   新参数用于终态或其它 kind 会在写入前被拒绝。
4. 新旧交界（升级重投）：结果级 id 命中时先按现有逻辑复用该事件（并复核载荷里的结果）。
   结果级 id 不命中时，在**同一 Activity、同一里程碑 kind、旧命名域**（`lexp:` 且不是
   `lexp:result:`）内按 `owner_confirmed_context` 精确比较 owner 的 artifact+version：
   - 找到与本次结果相同的旧事件 → 复用**最早**的那一条（排序键为
     `(activity_revision, handoff_id)`，与当前 revision 无关），返回
     `reused_legacy_handoff: true`，不新建、不改写旧载荷；
   - 旧事件都是可比较的、但没有相同结果 → 允许新结果走新 id 独立交接，旧事件原样保留；
   - 范围内任一旧事件读不回来，或没有可比较的 `owner_confirmed_context` → 具名拒绝。
     不能把“当前 revision 没找到”当成“没有同结果”，也不用标题或其它字段猜。
   查找只走既有 `handoff_events_by_id`，不开新索引、不迁移旧事件、不扫其它 Activity。
   当旧事件与新命名域事件都指向同一结果时，结果级 id 优先；这个选择只取决于事件是否存在，
   不随 revision 变化。同一结果有多条旧事件时仍只复用最早一条，不制造第三条。
5. 新 id 命中时再比较一次载荷里的结果：若同 id 却对应另一个 owner 结果（损坏/碰撞），
   明确拒绝，不静默重放错误旧结果。
6. 正式 cycle 只在“真实 owner 结果已读回且确证”的进度出口传入该参数：`SUCCEEDED` 且
   owner 返回了新版本、`_owner_confirmed_context` 非空。UNKNOWN、失败、无 step、
   `version_created: false` 等路径不产生新参数，也不新造进度结果。
7. AG-2 仍然只签发交接/保留待投队列；Memory consumer、M0 准入、治理、去重和
   `source_ref` 语义全部不动。不同 handoff 不保证被准入，拒绝/DEFER 不会被改写成功。

明确不改：Activity/Decision/Action 策略、Memory 准入门、AG2 状态机、既有事件字段、
LR2/LR4/AR0/AR1、默认 gate 与预算；不迁移旧实例，不新增持久 usage 字段。

## Alternatives considered

- **保持 Activity+revision 粒度（原方案 B）**：零改动，也永远不会有“同 Activity 两个结果
  被合并”的歧义。否决：这正是实际观察到的合并，且用户批准为确证结果增加身份；保留原状
  无法区分两个真实作品版本。旧粒度并未被删除，缺省调用继续使用它。
- **用 Decision id 做身份**：Decision 已有正式引用，取用方便。否决：同一结果可以在新的
  Decision 下重交，同一 Decision 也可能对应重试；Decision 不是“结果”，无法保证
  同结果同 id、异结果异 id。
- **把 Activity revision 在每次 CONTINUE 时递增**：id 自然分开。否决：CONTINUE 在 LR-2
  合同里就是状态/revision 的 NOOP；为了让去重好看而推进 revision，会改变 Activity 语义，
  并污染所有依赖 revision 的读取方。属于用假进度换 id。
- **新增持久 `result_identity` 字段或迁移旧事件**：表达最直接。否决：需要新 schema、
  旧数据迁移和跨版本读兼容；现有 `owner_confirmed_context` 已经携带 owner 的
  artifact+version，足够计算身份，不需要第二份持久事实。
- **扫描同 Activity 的全部历史事件做新旧比较**（最终采纳，限定在既有 handoff map 内）：
  最强立论是——旧身份按 revision 铸造，而 checkpoint/PAUSE/RESUME/WAIT 这类合法迁移会推进
  revision 而不产生新结果，只看当前 revision 必然把“更早写下的同结果旧事件”漏成“没找到”，
  再以唯一的 mailbox 行新增第二条交接，直接违背“同一结果一条经历”的兼容要求。
  最初以“不扩大扫描”为由否决，但那条否决只对“全库/跨 Activity 扫描”成立：本实现只在
  AG-2 已加载的 `handoff_events_by_id` 上按 Activity+kind+旧命名域过滤，代价是一次 O(n)
  遍历（n 为该 store 的 handoff 条数），且只有结果级 id 未命中时才发生。仍未采纳的是：
  新建索引/映射表、持久化 usage、跨 Activity 或 Memory 扫描、迁移或重写旧载荷。

## Consequences

- 同一 Activity 的两个（或多个）真实 owner 版本可以各自形成一条 handoff；Memory 是否
  准入仍由既有规则独立决定，不保证“每个结果一条经历”。
- 旧调用、旧事件、旧终态身份的读/重投语义不变；不需要迁移，也不会因为修复本身新增事件。
- 新 id 只对携带 `owner_confirmed_context` 的进度调用生效；没有确证上下文的进度仍落在
  旧粒度，不会被自动升级成“重大进展”。
- 跨 revision 的旧事件参与新旧比较：同一 Activity 的旧命名域事件按 owner 结果精确比对，
  最早匹配者被复用，事件身份与载荷保持不变。代价是一次针对既有 handoff map 的 O(n) 遍历，
  只在结果级 id 未命中时发生；不新增索引或持久字段。若范围内存在读不回或不可比较的旧事件，
  本次结果级交接被具名拒绝——宁可停下也不在“查找不完整”时新增第二条 handoff。该拒绝不会
  自动清理旧事件，需要人工核对，这是刻意的边界而非自动修复。
- 只证机制与隔离真 owner 链；不证真实模型自然采用、G1、24h 或产物在所有故障路径下的
  恢复。相关实现证据见 `scripts/v05_progress_result_identity_smoke.py` 与
  `tests/test_v05_progress_result_identity.py`。
- 与既有的确证结果通道互链：
  [practice review/method](2026-11-12-v04-practice-life-review-and-method.md)、
  [Memory product service](../feature/2026-10-27-v04-memory-product-service.md)。

## 跨进程重开（2026-10-25 晚）

结果级身份现在也在**两个真正不同 PID** 的同根链里验过一次：P1 写 v2/v3 两个确证结果后
关闭 runtime、关停自己的 Supply owner 并退出（关停走生产顺序：`shutdown`→`server_close`
→有界 `drain`→`prepare_socket_path` 守卫只清自有已停 socket，不裸 unlink）；P2 重开同一个新根的 canonical db 与 AG-2
store，先纯读（事件数/经历数/版本数不变），再合法 CONTINUE 同一 Activity 写出 v4，
得到新的 `lexp:result:` 身份；随后用既有 `emit_experience_handoff` 重投 P1 的同一确证
结果，仍是原 id、事件与经历不增、不重放 Decision/Action/writer。旧事件命名域、缺失
`progress_result_ref` 的调用与终态身份没有改动。证据：
`tests/test_v05_restart_continuation.py`、
`scripts/v05_restart_continuation_smoke.py`；交付说明
[v05 restart continuation](../../../../docs/engineering/v05-restart-continuation-20261025.md)。
