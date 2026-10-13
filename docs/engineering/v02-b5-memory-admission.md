# v0.2 B5 / E07 核查：真实经历进入 Native Memory 的现状与最小接线

**性质：只写代码里实际存在的东西。** 核查日期 2026-10-10，分支 `v0.2-autonomous-life`，基线 HEAD `ae661d4`。
所有行号来自本次实际读取 `components/native/**`、`chiyo_bundle/**` 与 `components/life/**`。

---

## 1. 结论先行

**AG-2 → Experience 的投递侧已经完整，Native Memory 的准入侧没有非聊天入口。**

- 投递侧：`LifeIntegrationCoordinator.emit_experience_handoff()`（`life_integration_ag2.py:4314`）已经能生成
  exactly-once 的 `LifeExperienceHandoffEvent`，`dispatch_experience_handoffs()`（`:4445`）已经实现
  投递、`DUPLICATE` 处置、失败不回滚、pending 重试。生产装配里装的是
  `RefusingExperienceConsumer`（`life_runtime_production.py:149`），所以这条链**从来没有真实出口**。
- 准入侧：M0 只允许**两种** `source_origin`（`src/app/evidence.py:19-20`），dataclass 白名单
  （`:66-87`）与数据库 CHECK（`:177-187`）双保险，全仓**没有第三个 writer**。
  `Authority.TOOL_OBSERVED_EVIDENCE` / `VERIFIED_EXECUTION_OUTCOME` 只存在于枚举
  （`runtime.py:41-42`）、消费策略与合成 fixture 里，生产链路没有任何代码把行动结果写进 M0。
- 召回侧（**这是真正的拦路石**）：`ProductionNativeMemoryResolver.resolve_current_turn()`
  （`production_resolver.py:318`）只认 `source_origin=USER_VISIBLE_INPUT ∧ delivery_status=RECEIVED
  ∧ conversation_id 匹配`（`:328-331`），并且要求 sealed `classify_intent` 判 P1/P2（`:337-340`）
  与 `age <= recency_seconds`（默认 900 秒，`:206-208`）。
  **即使把行动结果写进 M0，它也永远不会被召回**——召回在结构上就是「聊天轮次绑定」的。

## 2. 逐项回答细则第一节的问题

| 问题 | 事实 | 证据 |
|---|---|---|
| 什么来源能被正式接纳 | 只有两种 origin；dataclass + DB CHECK 白名单 | `evidence.py:19-20, 66-87, 177-187` |
| 谁负责写入 | 唯一落库点 `EvidenceStore.append(event)`；对外 `EvidenceWriter.append(event)->str`（`inserted/duplicate/queued/failed`） | `evidence.py:234`、`:549` |
| 接收 / 准入成功 / 拒绝如何区分 | 返回字符串状态 `inserted / duplicate / queued / failed`；M0 唯一索引 `(source_origin, primary_source_ref_id)` 去重，同 ref 不同内容 → `ValueError` → `failed` | `evidence.py:189-191, 241-250, 275-284` |
| 如何按 subject / source / 可见范围检索 | **没有 per-subject 列**（只有恒为 `"chiyo"` 的 `memory_owner`）；溯源靠 `source_refs:[{kind,id}]`（落库压平成 `primary_source_ref_id` + `source_refs_json`）；可见范围是召回时现算的 | `evidence.py:163-176`、`production_resolver.py:163-172` |
| 纠正 / 删除 / 禁止召回如何影响后续 | `/chiyo_memory correct|delete` → `memory_control.py:155 command()` / `:117 apply()`，落 `memory_controls` 表 + append-only 触发器；召回时 `memory_control.py:36 recalls()` 命中 `source_event_ids ∩ blocked_events` 即整条丢弃；`:188 projection()` 连整个 turn 的 sibling 与更早的 chiyo 历史一起 block | `memory_control.py:95-103, 36-55, 188-235`；调用点 `hermes_plugin.py:254-289` |
| 现有 Activity/Action 结果能否通过正式接口投递 | **不能**。最接近的是 `m37_m0_bridge.py:283 write_user_event(...)`，但它硬编码 `USER_VISIBLE_INPUT` + `speaker=user`（`:297-300`），语义上是「用户说了话」 | `m37_m0_bridge.py:283-311` |

**幂等**：M0 按 `(source_origin, primary_source_ref_id)` 去重，明确注释「Dedupe is by SOURCE IDENTITY,
never by content hash」（`m37_m0_bridge.py:33-40`）；M1 按 `processed_evidence.event_id` PK 去重
（`m1.py:310-314, 412-416`）；M3 按 `evaluations.trigger_evidence_id UNIQUE`（`m3.py:272, 359-364`）。
**注意**：换一个 `source_ref` 投同样内容会得到新事件，因此 handoff 侧必须用稳定 ref（AG-2 已经做到：
`lexp:` id 由 `(activity_id, kind, revision)` 派生，`life_integration_ag2.py:4339-4344`）。

## 3. 因此 B5 的最小正确接线（两段，各自可独立验收）

**段一：投递（不需要改 Memory owner）**
1. 在 cycle 里，作品版本真实落地后调用既有 `emit_experience_handoff(..., terminal_or_milestone_kind=HANDOFF_KIND_SIGNIFICANT_PROGRESS, artifact_refs=[...])`——
   现在只有 COMPLETED/ABANDONED/CONFLICT 会触发（`ag2:5200/5229/5589/5605`），「真实创建 / 真实修改 / 有证据的进展」缺一个触发点。
2. 写一个真实 consumer，把事件投给 Memory 准入端口；端口不可用时如实返回 `DEFERRED`（AG-2 已经会保留 pending 并重试，`:4451-4500`）。

**段二：准入（需要动 Memory owner 一处，属于细则授权的「窄适配器」）**
- 在 M0 增加**第三种** `source_origin`（例如 `LIFE_VERIFIED_OUTCOME`）并同步 dataclass 白名单与 DB CHECK，
  写入仍走唯一的 `EvidenceStore.append`，不新建存储。
- 这只解决「记住」，**不解决「召回」**：resolver 的聊天绑定不会看到它。

**段三（真正的设计决定，需要你点头）**
让下一轮决策/内容上下文能读到这条经历，有两条路，代价差别很大：
- **A. 扩 Memory 的召回契约**：让 resolver 能按非聊天来源召回行动经历。这等于修改冻结的记忆 owner 与它的
  召回语义（M3 的 intent/时序门、`ScopedVisibleReader`、消费侧 8 道门都要动），**超出「一个窄适配器」**。
- **B. 由 Life 侧只读消费**：cycle 通过 M0 的既有读 API 取「本主体最近的已准入行动经历」，把**有界摘要**
  放进内容/决策上下文，并让纠正/删除状态对它同样生效（`memory_control.recalls()` 是现成的过滤器）。
  Memory 只提供经历与评价，**不覆盖 Artifact 当前 head，也不绕过 AG-1/AG-2 执行动作**。

**我的建议是 B**：它满足细则第三节「Memory 经历影响未来」与第五节「纠正/删除后不得再进入上下文」，
不修改冻结的召回契约，也不建平行存储；A 应该作为单独一轮、明确批准后再做。

## 4. 明确未做（NOT_RUN）

- 段一/段二/段三的代码与验收（A/B 因果对照、纠正与删除验收、真实模型 6 次对照）**尚未实现**。
  本节只交付细则第一节要求的核查与最小接线设计。
- 未触碰任何线上个人记忆；未在真实 profile 上跑记忆写入。
