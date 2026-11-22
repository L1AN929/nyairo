# v0.8 Wave 7 · 0.2 / 0.3 设计债四项最小实装收口

> 批次：`v08-wave7-v02-v03-design-debt`（2026-11-15）
> 登记依据：`docs/engineering/20261114-design-debt-registration.md` §一.2 / §一.3 / §一.5 与 §二 SlotQuota 行。
> 原则：最小、扎实、可机验；不改对外 HTTP 契约、不新增依赖、零产品大模型调用（`llm_calls == 0`）。

## 1. 本批解决什么

此前这四项只在设计稿或半截状态：

1. **P12 自传体叙事**没有实现，记忆侧只有 event/belief owner；
2. **有界 Inquiry** 没有「问题→证据→收敛/停止」状态机；
3. **Ch.16 22 类一年衰败监测矩阵**没有可读投影；
4. **Ch.07 §4 SlotQuota / DailyEnvelope** 只有日 token 预算，没有时段槽位与日分类包络。

本批为每项提供最小生产实现与测试验收，同时保持既有 0.2 视图、冻结锚点与默认预算路径 100% 兼容。

## 2. 四项实现

### 2.A P12 自传体叙事只读投影

- 函数：`components/life/scripts/v02_recall_projection.py::project_p12_autobiographical_narrative(
  state_root, *, has_subject_capability=False, now_iso=None)`
- 数据源（全部只读已有磁盘事实，不新建 store）：
  - `memory/v02_recall_projection_state.json` 的情景记忆；
  - `memory/v02_common_ground_state.json` 的共同认知条目；
  - `state/v02_agency_depth_state.json` 的选择片段、二阶小规矩、已收敛 Inquiry；
  - `phone/workbench_tray.json` / `phone/workbench_curio.json` 的作品托盘。
- 输出：`state_exists`、`perspective`、`observed_at`、`state_status`、`chapters`、
  `turning_points`、`recurring_motifs`、`narrative_horizon`、`attested_source_refs`、
  `attested_event_count`、`llm_calls=0`、`zero_write=True`。
- 诚实边界：空实例返回空章节/转折/母题与 `state_status=NO_STATE_FILES`；
  有文件但无实证事件返回 `STATE_PRESENT_NO_ATTESTED_EVENTS`；`attested_source_refs`
  只来自真实条目 id，绝不编造。
- 隐私门控：`has_subject_capability=False` 时过滤未共享（`shared=False`）、主动淡忘与
  私密记忆、`PRIVATE_UNSHARED` 共同认知、未共享或未收敛 Inquiry；只保留可公开事实。
- 挂载：`read_v02_recall_projection_view(...)` 新增 `has_subject_capability` 参数并输出
  `p12_autobiographical_narrative`；`read_v02_agency_depth_view` 在引擎投影之上挂同一字段。

### 2.B 有界 Inquiry 生命周期状态机

- 引擎：`components/life/scripts/v02_agency_depth_engine.py`
  - 常量：`MAX_OPEN_INQUIRIES=5`、`MAX_EVIDENCE_NOTES_PER_INQUIRY=8`、
    `INQUIRY_STATUSES = {OPEN, INVESTIGATING, CONCLUDED, STOPPED}`。
  - 三个导出函数（受控改写，经 `apply_agency_depth_mutation`，`actor_side=subject`）：
    - `open_bounded_inquiry(...)`：创建 `OPEN`；活跃条数达 5 时 `409 INQUIRY_CAPACITY_EXCEEDED`。
    - `record_inquiry_evidence(...)`：追加真实观察笔记并 `OPEN -> INVESTIGATING`；
      单条达 8 时 `409 INQUIRY_EVIDENCE_CAPACITY_EXCEEDED`；已终态 `409 INQUIRY_ALREADY_SETTLED`。
    - `conclude_or_stop_inquiry(...)`：收敛到 `CONCLUDED`（需 `conclusion`）或 `STOPPED`
      （需 `stop_reason`），写 `settled_at`；已终态再迁移 `409 INQUIRY_ALREADY_SETTLED`。
  - 投影：`project_v02_agency_depth` 新增 `inquiries`，伴侣视角按 `shared` 过滤。
  - 摘要：`summarize_agency_depth_state_for_context` 新增 `active_inquiries`。
- 视图：`v02_agency_depth_view` 放行三个 subject-only action 并加入
  `_MIRRORED_DATA_KEYS`（`inquiries`、`p12_autobiographical_narrative`）。
- 日常循环桥接：`v02_v03_daily_loop_bridge.py`
  - `collect_v02_v03_loop_candidates` 为活跃 Inquiry 生成
    `v02_inquiry:<inquiry_id>` 候选，`action_family = "advance_bounded_inquiry"`；
  - `V02_DAILY_FAMILY_TO_KIND` 映射到 `ADVANCE_BOUNDED_INQUIRY`；事件只带描述符事实；
  - `select_deterministic_loop_action` 增加该候选的优先级谓词；
  - `settle_v02_v03_round_consequences` 的 `_V02_DAILY_SETTLE_HANDLERS` 新增
    `_settle_v02_advance_bounded_inquiry`：追加一条由 Decision 决定的确定性观察笔记
    （`decision_marker`），经引擎 `record_inquiry_evidence` 落盘，并写入既有
    `v02_v03_consequence_ledger.json`；同 Decision/operation_key 重放幂等。

### 2.C Ch.16 22 类年度衰减监测矩阵

- 位置：`components/life/scripts/v02_runtime_governance_migration.py`
- 注册表：`DECAY_MONITOR_CATEGORIES_22`，四域恰好 22 类：
  - `memory_recall` 6：episodic_traces / autobiographical_anchors / sensory_impressions /
    promise_commitments / seasonal_milestones / active_faded_items
  - `relationship_common_ground` 5：shared_vocabulary / agreed_boundaries /
    pending_clarifications / gift_lantern_entries / co_presence_habits
  - `agency_habits_projects` 6：preference_satiation / open_inquiries / tentative_plans /
    tomorrow_notes / workbench_drafts / second_order_rules
  - `world_rhythm_somatic` 5：courtyard_plant_care / neighborhood_familiarity /
    illness_recovery_memory / somatic_comfort_baseline / economic_spending_rhythm
- 投影：`project_annual_decay_matrix_22(state_root, *, horizon_days=365, now_iso=None)`；
  每类输出 `status`（ACTIVE / DORMANT / NEEDS_REANCHOR / ABSENT_BASELINE）、
  `half_life_days`、`floor_weight`、`drift_band`、`retained_weight`、`re_anchor_policy`、
  `present`、`entry_count`、`last_observed_at`、`age_days`、`sources`；
  汇总 `total_categories`、`group_counts`、`groups`、`status_counts`；零写盘、`llm_calls=0`。
- 状态判定：无基线文件 → `ABSENT_BASELINE`；`age_days > horizon_days` → `NEEDS_REANCHOR`；
  `age_days > horizon_days/2` → `DORMANT`；否则 `ACTIVE`。
- 挂载：runtime-migration 投影新增 `annual_decay_matrix_22`，视图 `_MIRRORED_DATA_KEYS` 同步。

### 2.D Ch.07 §4 SlotQuota 与 DailyEnvelope

- 位置：`components/life/scripts/life_request_budget.py`
- 常量（写入 `__all__`）：
  - `SLOT_QUOTAS = {"DAYTIME_ACTIVE": 2, "EVENING_WIND_DOWN": 1, "NIGHT_REST": 0}`
  - `DAILY_ENVELOPE_CAPS = {"daily_hard_cap": 8, "per_category_cap":
    {"INITIATION": 3, "CONTINUATION": 5, "REFLECTION": 2}}`
- API（`LifeRequestBudget`）：
  - `check_slot_and_daily_envelope(slot_kind, category, now_dt=None)`：只读联合检查，
    fail-closed；非法槽位/分类返回 `REQUEST_BUDGET_UNKNOWN_SLOT_OR_CATEGORY`；
  - `record_slot_and_daily_consumption(slot_kind, category, now_dt=None, operation_key=None)`：
    双上限通过后持久化，超限抛 `RequestBudgetError`（槽位 / 日包络 reason code 区分）；
    同 `operation_key` 重放幂等（`already_applied=True`）；
  - `slot_and_daily_snapshot(now_dt=None)`：只读用量快照，损坏账本不抛异常；
  - `can_spend(..., slot_kind=None, category=None, enforce_slot_envelope=False)`：
    默认与既有 `check()` 完全一致；仅显式 `True` 时联合校验。
- 向后兼容：`slot_envelope_days` 是**可选**账本字段；旧账本无该字段照常读取；
  默认 `reserve()` 路径不写该字段，账本形态与既有完全一致；跨天按业务日新起桶；
  活跃日单调不因回拨重置（沿用既有 `_active_day` 语义）。

## 3. 验证

```
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest \
  tests.test_v08_v02_v03_design_debt_closure \
  tests.test_v02_agency_depth \
  tests.test_v02_runtime_governance_migration \
  tests.test_v02_phone_http_surfaces \
  tests.test_v02_residual_and_ch19_closure \
  tests.test_autonomous_life_budget_wiring \
  tests.test_release_metadata -v
```

结果：`Ran 130 tests ... OK`。新增 `tests/test_v08_v02_v03_design_debt_closure.py`
27 条，覆盖：

- P12：真实实例章节/转折/母题/溯源；空目录零捏造；伴侣视角隐藏私密项；零写盘；两个读面挂载。
- Inquiry：完整状态机；5/8 上限；同 key 幂等；collect → event → settle → 桥接 tick 选择与落盘；
  证据满额时命名拒止；伴侣越权 403 与私密探究过滤。
- 衰减矩阵：22 类唯一、四域 6/5/6/5；空实例全 `ABSENT_BASELINE`；真实文件 ACTIVE；
  旧 mtime → `NEEDS_REANCHOR`；视图挂载；零写盘与字段合法性。
- 预算：常量精确；槽位/分类/日硬上限各自独立拒止；operation_key 幂等；跨天隔离；
  `enforce_slot_envelope=False` 与 `check()` 全等；旧账本无可选字段照常读取。

## 4. 已知边界与未验证项

- **SlotQuota 不是原四时段设计**：本批按任务书给定三槽位闭集落地，未实现原设计的
  6/7/7/4 四时段 × 12500/14000/14000/9500 与 `carry_ratio=0.5` 结转；原设计仍在
  `20261114-design-debt-registration.md` §二登记为未裁定，不因本批视为追认。
- **P12 / 衰减矩阵是单点只读投影**：不建独立 store，不做版本化叙事快照或季度人工巡检归档。
- **Inquiry 未含候选解释列表与独立预算字段**：容量按活跃条数（5）与单条证据数（8）硬限。
- **`run_v02_v03_daily_tick` 在本工作树存在既有签名不匹配**：
  `v03_dwell.evaluate_host_gap` 不接受 `previous_host_iso`，故本批测试改用
  `collect + select_deterministic_loop_action + round_event + settle` 的真实同一接缝；
  Cycle/Resident 复用同一 `round_event`/`settle` 机制（Wave 6 已有 seam 测试）。
  该 `run_tick` 不匹配不属于本批写集，未修改。
- **线尾规范化**：`components/life/scripts/autonomous_life_cycle.py` 在本批开工前的工作树里
  已有 CRLF（HEAD 为 LF），导致 `test_v02_residual_and_ch19_closure` 的 LF 卫生用例红。
  为通过本批指定验收，仅把该文件 CRLF 机械归一为 LF（零行为改动）；未改其逻辑。
- 未改 `autonomous_life_assembly.py` / `life_sharing_service.py` /
  `readonly_recall_adapter.py`；未引入产品大模型调用。
