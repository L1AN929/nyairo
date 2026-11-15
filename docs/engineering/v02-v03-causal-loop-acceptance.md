# 0.2 + 0.3 自主日常主循环生产者-消费者闭环与因果反证验收报告

## 1. 交付定位与边界声明

本交付面向《14-研发工作包与验收体系》五组验收（**V-A 至 V-E**）及第 6 节严格受控因果反证规范，将 `0.2`（36 项生活细节 + 双层回忆灯）与 `0.3`（生病能力包络、自立经济、三时钟慢变量与闪念蒸发、无意义发呆、主体拒绝立场锁、双手占用与附着共在、邻里 NPC 隐私防复活）接入自主日常主循环（`AutonomousLifeResident`），构建**纯零写入候选采集（生产者）→ 确定性策略/决策门控 → 仅凭真实 `APPLIED` 回执结算后果（消费者）**的完整闭环。

### 冻结锚点零改动校验（SHA-256）

| 冻结文件 | 预期 / 实测 SHA-256 | 状态 |
| --- | --- | --- |
| `components/life/scripts/autonomous_life_assembly.py` | `76b4c7cbcdc29154851fedcd2b962f93549092c35f8feeedec29bfc93169fa5a` | 零改动通过（`test_01` 硬校验） |
| `components/life/scripts/life_sharing_service.py` | `0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038` | 零改动通过（`test_01` 硬校验） |

### 交付文件清单

1. `components/life/scripts/v02_v03_daily_loop_bridge.py`：`0.2 + 0.3` 自主主链生产者-消费者桥接核心、门控引擎、三态技术可观测分类器与撤权防复活对账器。
2. `components/life/scripts/autonomous_life_resident.py`：向后兼容轻量挂钩（当实例无 `phone/*.json` 且未显式开启时 **100% 零副作用**，确保既有 `v0.7` 主链回归零变化）。
3. `tests/test_v02_v03_causal_counterfactual_soak.py`：**22 项**全确定性验收测试套件（全程 `llm_calls == 0`）。
4. `docs/engineering/v02-v03-causal-loop-acceptance.md`：本工程映射与验收证据文档。

---

## 2. 生产者-消费者闭环映射表

### 2.1 生产者侧：`collect_v02_v03_loop_candidates(day_root, *, now_iso=None)`（100% 纯零写入）

当 `<day_root>` 或 `phone/*.json` 不存在时绝不创建任何目录或文件；存在真实生活状态时，提取具备结构化 `source_ref`、`causal_parent_refs` 与 `provenance_refs` 的 `AG-0 CandidateRecord`（`SOURCE_PERSONAL_OPPORTUNITY`）及有界决策上下文：

| 层级 | 生活状态源（只读） | 产出候选 / 门控语义 | 关键字段与溯源锚点 |
| --- | --- | --- | --- |
| `0.2` | 床头明日便签（`FutureItems` / `desk_presence.tomorrow_note`） | `follow_tomorrow_note` | `future:<id>` + 关联 `artifact:<id>` |
| `0.2` | 共同待定小约定（`home_hearth` `tentative_plans` 中 `PROPOSED/TENTATIVE`） | `adopt_tentative_plan` | `plan:<plan_id>`（自动过滤 `DROPPED/EXPIRED`） |
| `0.2` | 创作台断点托盘（`workbench_curio` 未完草稿、参考图与材料） | `advance_workbench_draft` | `artifact:<id>` + `baseline_version_id` |
| `0.2` | 书店橱窗收藏与散步路线/市集心愿（`outing_bench_bookstore` / `outing_routes_market`） | `read_bookstore_window` / `strenuous_outing_walk` / `non_essential_purchase` | `book_slot:<idx>` / `walk_route:<id>` / `market_wish:<code>` |
| `0.2` | 绿植观察手账（`longterm_room_habits` `plant_sketch_links`） | `observe_plant_journal` | `plant:<plant_id>` + `artifact:<id>` |
| `0.3` | 生病能力包络（`v03_ecology.illness.capability_envelope`，`ADR-041`） | `prodromal(7)` / `convalescent(6)` / `acute(3)` 收缩 `candidate_cap`；`blocks_strenuous_outing == True` 硬拦截外出并产出 `illness_rest_recovery`（卧床/温水）；`blocks_adult_intimacy == True` 标记亲密硬阻断 | `v03_care:bed_rest_block` / `v03_care:warm_hydration` |
| `0.3` | 自立经济（`v03_ecology.economy`，`ADR-039`） | `budget_tight == True` 或存在未完成 `savings_goals` 时过滤超预算非必需消费（`put_back_tight_budget` / `save_for_later`，严守 `begs_partner_when_tight == False`），产出独立稿费/校对兼职候选 | `v03_job:manuscript_fee` / `v03_job:proofreading_shift` |
| `0.3` | 主体拒绝立场锁（`autonomy_rhythm` `REFUSED_FIRM`） | 硬拦截被拒议题候选，连催不改口（`capitulation_allowed == False`） | `REFUSAL_STANCE_LOCKED` |
| `0.3` | 无意义发呆 & 书桌附着共在（`v03_clock` / `v03_embodied_social`） | 产出零效率惩罚的 `meaningless_idle`；伴侣 `beside_at_desk` 附着共在保持 `foreground_activity_interrupted == False`，右手持物与双手占用如实投影 | `v03_idle:daydream_window` |

### 2.2 消费者侧：`settle_v02_v03_round_consequences(day_root, round_event, *, now_iso=None)`

严格执行**回执权威门控**：仅当授权执行侧（`subject` / `her` / `life_resident` / `world_executor`）携带 `effect_state == "APPLIED"` 与非空 `receipt_ref` 时才允许落账；遇到 `UNKNOWN`、`FAILED`、`REFUSED`、缺失回执或 `partner` 越权伪造回执时，**100% 拒绝乐观写入（`optimistic_writes == 0`）**，并通过持久化 `v02_v03_consequence_ledger.json` 保障 `operation_key` 幂等重放与意图冲突拦截（`INTENT_CONFLICT`）。

| 已确认主链动作（`APPLIED`） | 消费者回写切片与闭环后果 |
| --- | --- |
| 外出非 `home` 场所移动/停留（`OUTING_MOVE` / `OUTING_STAY`） | 调用 `sync_v02_slices_from_committed_world` 并同步熟悉长椅到访计数与散步路线足迹（`outing_bench_bookstore` / `outing_routes_market`） |
| 接续床头明日便签 / 创作台草稿提交新版本（`FOLLOW_TOMORROW_NOTE` / `ADVANCE_WORKBENCH_DRAFT`） | 核销 `FutureItems` 便签为 `DONE`、推进创作台托盘 `baseline_version_id`，在不篡改 `v1` 原稿的前提下写入双层回忆灯 `later_reflection` |
| 静养休息（`REST`） | 联动 `v03_ecology_view` 记录 `bed_rest_block` / `warm_hydration` 并按 `rest_mode` 推进多日真实病程恢复曲线（严禁瞬间清零秒愈） |
| 独立撰稿 / 校对兼职交付（`COMPLETE_INDEPENDENT_WORK`） | 联动 `complete_independent_work` 真实入账钱包与工分回执 |
| 跨时段 / 跨夜推进（`CROSS_NIGHT_ADVANCE` / `ADVANCE_CLOCK`） | 联动 `v03_clock_evaporation` `advance_projection`：6h 外衣风干为 `dry`、30m 热饮自然转凉、72h 想念留话零缺席债淡出、`EVAPORABLE` 闪念零墓碑物理蒸发、`UNKNOWN` 保持未决绝不脑补（`cognitive_backfill_count == 0`） |
| 无意义发呆（`MEANINGLESS_IDLE`） | 记录零生产力惩罚（`productivity_penalty == 0`），不强制生成作品或分享 |

---

## 3. 五组验收（V-A 至 V-E）与 2×2 因果反证证据矩阵

测试文件：`tests/test_v02_v03_causal_counterfactual_soak.py`（共 **22 个测试**，全部零真实模型调用 `llm_calls == 0`）。

### V-A 权威与事实边界（`test_01`, `test_04`..`test_08`）
- **冻结锚点完整性（`test_01`）**：逐字节校验 `autonomous_life_assembly.py` 与 `life_sharing_service.py` 的 SHA-256。
- **未决/失败绝不乐观推进（`test_04`）**：`effect_state="UNKNOWN"` 或 `"FAILED"` 时，磁盘快照前后完全一致，零乐观落账。
- **缺回执与越权拦截（`test_05`）**：无 `receipt_ref` 或 `actor_side="partner"` 伪造主体动作时硬拒绝。
- **幂等重放与冲突保护（`test_06`..`test_08`）**：相同 `operation_key` 重放返回 `already_applied=True` 且不双计入账/足迹；篡改负载复用同键报 `INTENT_CONFLICT`。

### V-B 持久、崩溃冷重启恢复与跨 3 天连续推演（`test_09`..`test_11`）
- **跨 3 天零 LLM 连续推演 + 冷重启（`test_09`）**：
  - **Day 1 晚（21:30）**：淋雨回家挂起微潮风衣（`damp`）、写下床头明日便签、记录 72h 想念轻话、写入 `EVAPORABLE` 雨痕闪念、登记邮筒 `UNKNOWN` 未决动作、累积淋雨受凉负荷；
  - **跨夜 +12h 至 Day 2 晨（09:30）**：零 LLM 跨夜结算后，风衣满 6h 自动变 `dry`，`EVAPORABLE` 闪念从磁盘物理消失且 `tombstone_count == 0`，邮筒 `UNKNOWN` 保持 `UNKNOWN`（`unknown_actions_guessed == 0`、`cognitive_backfill_count == 0`）；
  - **Day 2 冷重启**：重新从同一 `day_root` 采集候选，确定性拾起昨日到期便签推进作品 `v2` 并记下事后回望；午后熬夜叠加受凉进入 `acute` 发烧期，主链自动拦截高耗能项并转向卧床静养；
  - **Day 3 晚（+73h）**：想念留话超 72h 自然淡出且 `no_absence_debt == True`；病程沿曲线平稳恢复至康复期；双层回忆灯 `then_only` 严格隐藏事后回望、`with_later_context` 呈现回望，且 `v1` 原始正文一字未改。
- **30 分钟热茶自然转凉与崩溃后 `UNKNOWN` 保持（`test_10`）**：`hot_tea` 经 `+35m` 从 `warm` 转为 `room_temp`；饮用回执未决崩溃重启后分类器精准识别为 `PENDING_UNKNOWN_SUSPENDED`。
- **三阶段病程候选容量收缩（`test_11`）**：`prodromal(7)` → `convalescent(6)` → `acute(3)` 严格按包络收缩候选集上限。

### V-C 生命周期撤权与删除防复活（`test_12`..`test_14`）
- **撤回分享防复活（`test_12`）**：撤回已送达分享（`DROPPED`）后，重跑 `sync_v02_slices`、重建分享索引与重放幂等键，冰箱贴（`window_keepsake`）、外出作品展（`outing_gallery`）与双层回忆灯均降级为 `withheld`，正文零泄露。
- **源作品归档与墓碑治理防复活（`test_13`）**：源项目归档或进入墓碑治理态后，双视角（`partner` / `subject`）回忆灯与画廊均绝不复活已删源文本。
- **邻里 NPC 撤权脱敏防复活（`test_14`）**：调用 `revoke_npc_shared_ref` 后，NPC 活跃谈资清空、已知条目状态置为 `withheld_revoked` 且摘要抹除，重跑同步绝不复活。

### V-D 四组单变量 2×2 受控因果反证（`test_15`..`test_18`）
每组实验均从同一基底目录克隆出 **State A** 与 **State B**，固定其余全部状态，**仅对单一语义源施加合法状态干预**（绝不靠删文件或制造异常凑数）：
1. **明日便签因果对（`test_15`）**：State A（有床头明日便签）选择 `follow_tomorrow_note` vs State B（无便签，其余完全同构）选择 `adopt_tentative_plan`。
2. **急性生病能力包络因果对（`test_16`）**：State A（`healthy`）放行并选择远郊徒步路线、`adult_intimacy_blocked == False` vs State B（`acute` 发烧）硬过滤远途外出（`ILLNESS_BLOCKS_STRENUOUS_OUTING`）、`adult_intimacy_blocked == True`、转向 `illness_rest_recovery`。
3. **紧预算金钱重量因果对（`test_17`）**：面对同一件 `1800 JPY` 非必需海玻璃风铃心愿，State A（宽裕余额）判定 `afford_independently` 并独立购买 vs State B（余额逼近 `living_floor_cents` 紧预算）翻转为 `put_back_tight_budget`、转向 `independent_work` 自主赚钱，且全程 `begs_partner_when_tight == False`。
4. **主体拒绝立场锁因果对（`test_18`）**：State A（无锁）采纳夜间聚会提议 vs State B（声明 `REFUSED_FIRM` 后遭遇连续 3 次催促恳求，`push_attempts == 4`）过滤该提议并坚守 `HOLD_REFUSAL_STANCE_LOCK`，`capitulation_allowed == False` 绝不滑跪。

### V-E 低预算零 LLM 自转、三态区分与常驻主链挂钩（`test_19`..`test_22`）
- **三态可观测严格区分（`test_19`）**：技术观测无歧义区分 `QUIET_EVENTLESS_DAY`（安静无事日）、`PENDING_UNKNOWN_SUSPENDED`（存在未决 `UNKNOWN` 悬挂）与 `INTENTIONAL_REST_RECOVERY`（主动静养恢复），绝不把 `UNKNOWN` 猜成安静或休息。
- **书桌附着共在与无意义发呆（`test_20`）**：伴侣 `beside_at_desk` + 主体右手持钢笔（`free_hand_count == 1`）时不打断草稿推进（`foreground_activity_interrupted == False`）；结算 `MEANINGLESS_IDLE` 零效率惩罚、零强制产物。
- **常驻循环向后兼容与闭环激活（`test_21`, `test_22`）**：无 `phone/*.json` 时 `AutonomousLifeResident.tick()` 零桥接副作用；有 `phone/*.json` 时自动零写采集候选、拒绝 `UNKNOWN` 乐观结算、消费 `APPLIED` 回执完成落账，全程 `llm_calls == 0`。

---

## 4. 验收执行命令与实测结果

在 WSL Ubuntu 环境执行：

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/d/nyairo-rc1-body-backend && python3 -m unittest tests.test_v02_v03_causal_counterfactual_soak -v"
```

同时执行既有 `v0.7` 日常主链回归校验向后兼容性：

```bash
wsl -d Ubuntu -- bash -lc "cd /mnt/d/nyairo-rc1-body-backend && python3 -m unittest tests.test_v07_daily_life -v"
```

全部测试均以 **`OK`（`llm_calls == 0`，在线模型配额消耗 `0`）** 通过。
