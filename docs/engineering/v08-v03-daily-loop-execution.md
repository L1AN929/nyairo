# v0.8 Wave 5B · 0.3 日常循环执行与亲密断点恢复

本文件说明 0.3（照护 / 发呆 / 自主收入 / 可负担购买 / 成年亲密断点恢复）如何进入
v0.2/v0.3 自主日常循环。桥接层只做“真实状态 → 候选 → 确定性动作 → 幂等结算”，
所有状态写入仍由既有 owner 契约完成。

## 1. 上下文

Wave 5A 已完成 `v02_v03_daily_loop_bridge` 的候选采集、事件构造与结算接线，
但缺少端到端实测与文档。本批补齐：

- `tests/test_v08_v03_daily_loop_execution.py`：真实 owner 上的 0.3 日常执行测试；
- 一处最小修复：亲密断点在“换 Decision 重放”时曾被误判为意图冲突；
- 本文件与运行记录。

## 2. 0.3 候选（`collect_v02_v03_loop_candidates`）

| source_ref | 触发（真实状态） | 结算动作 | 关键事实 |
|---|---|---|---|
| `v03_care:bed_rest_block` |  illness `blocks_strenuous_outing` 或急性期 | `action_kind=REST` + `care_event_kind=bed_rest_block` | `record_strain_or_rest` + `advance_illness_recovery`，`rest_active=True`、`strain_load` 下降 |
| `v03_care:warm_hydration` | 同上 | `action_kind=WARM_HYDRATION` | 额外写入 `illness.last_hydration_at` |
| `v03_idle:daydream_window` | 任一 0.2/0.3 slice 活跃 | `action_kind=MEANINGLESS_IDLE` | 35B §9.1：`productivity_penalty=0`、`growth_score_delta=0`、不产 artifact/share、不强制总结 |
| `v03_job:manuscript_fee` | `economy_active` 且预算紧张或有未完成储蓄目标 | `action_kind=COMPLETE_INDEPENDENT_WORK` | 稿费入账，`effort=creative` |
| `v03_job:proofreading_shift` | 同上 | 同上 | 校对班次入账，`effort=routine` |
| `purchase_cue:<cue_id>` | `phone/v03_purchase_cues.json` 中存在正价条目 | `action_kind=COMMIT_AFFORDABLE_PURCHASE` | 可负担时扣款并记录 `budget_deliberations` |
| `market_wish:<wish_id>` | `outing_routes_market.json` 中存在带 `budget_cap_jpy` 的集市心愿 | 同上 | 以心愿预算为价格，可负担时扣款 |
| `v03_intimacy_resume:<permit_id>` | permit 的 `checkpoint.resumable=True`、`activity_ref` 非空、未消费 | `action_kind=RESUME_INTIMACY_CHECKPOINT` | 消费断点，记录 `resumed_activity_ref` / `consumed=True` |

0.3 候选与既有 0.4 邻里候选共用同一池、同一拒止/预算/疾病门控。购买门控在
`economy_active` 时生效：超预算的冲动条目以 `BUDGET_TIGHT_IMPULSE_FILTERED`
（`verdict=put_back_tight_budget`）拦在候选之外；存在未完成储蓄目标时以
`SAVINGS_GOAL_IMPULSE_FILTERED` 拦下。

## 3. 结算与幂等

`settle_v02_v03_round_consequences` 接受 `v02_v03_round_event_for_selected_candidate`
构造的事件：

- 普通 0.3 动作的 operation key 为
  `v02v03_daily_<sha256(raw_ref|decision_id)>`。同一 `decision_id` 重放命中
  consequence ledger，返回缓存结果 + `already_applied=True`；不同 `decision_id`
  是新的逻辑动作，可跨轮/跨日再次入账。
- 亲密断点的 operation key 只由 `permit_id + activity_ref` 决定
  （`v03_intimacy_resume_<permit>_<activity>`），因为“同一个断点只恢复一次”；
  消费后 `checkpoint.consumed=True` / `resumable=False`，再次采集不再产出候选。

### 3.1 最小修复：换 Decision 重放断点不再是冲突

修复前，亲密断点事件的 `decision_id` 参与了 consequence ledger 的 intent hash；
operation key 又固定不含 Decision，于是“同一断点、另一个 Decision”重放会命中同一
key 但 hash 不同，被判定为 `CONSEQUENCE_OPERATION_KEY_CONFLICT`。这与事件构造处
“重放（哪怕换了 decision）幂等”的设计说明不符。

修复：`settle_v02_v03_round_consequences` 在 `action_kind ==
RESUME_INTIMACY_CHECKPOINT` 时把 `decision_id` 排除出 intent hash。其他动作的 key
已编码 `decision_id`，行为不变。修复后换 Decision 重放返回 `already_applied=True`，
断点仍由首个消费的 Decision 记录，不会二次消费。

## 4. Cycle / Resident 接线

- `autonomous_life_cycle`：任何能被桥接事件构造器转成可执行命令的候选
  （`v04_*` 或 `v03_care/idle/job/intimacy_resume`、`purchase_cue:`、`market_wish:`）
  都走 `V02V03_DAILY` 有界步骤，结算后写入 `world_fact:v02v03_daily:<ref>` 完成事实。
- `autonomous_life_resident`：只对 `START` / `CONTINUE` 且 `ACTED` 的 Decision
  结算；由 Candidate 的 `target_refs` / `origin_source_ref` 反查桥接描述符。Cycle 与
  Resident 为同一 Decision 构造同一事件、共享 operation key，因此 Resident 的结算
  是幂等重放，不会二次写盘。

## 5. 测试

`tests/test_v08_v03_daily_loop_execution.py`：

- 照护：卧床 / 温水真实结算，重放幂等，新 Decision 再次生效；
- 发呆：零效率惩罚、无产物、无强制总结，可重放、可再执行；
- 自主收入：两种工作入账；同 Decision 只入账一次，换 Decision 再入账；
- 购买：`purchase_cue` 与 `market_wish` 可负担成交并出收据/结账记录；
  超预算条目不进入候选，伪造超额命令被 owner 拒绝（`INSUFFICIENT_INDEPENDENT_FUNDS`），
  余额不为负；
- 亲密断点：`save_activity_checkpoint` 保存；`stop_scene` 与 600s `IDLE_TIMEOUT`
  两条降级路径都使 `checkpoint.resumable=True`；采集器发现
  `v03_intimacy_resume:<permit_id>`；消费一次后不再产出；重放（含换 Decision）幂等；
- 真实 `AutonomousLifeCycle` + `AutonomousLifeResident` 脚本化认知（0 LLM）分别选中并
  结算照护、稿费与亲密断点。

全部断言 `llm_calls == 0`（脚本化 cognition 与 owner 读层均不调用产品模型）。

## 6. 验收

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest \
  tests.test_v08_v03_daily_loop_execution \
  tests.test_adult_intimacy_co_presence_wiring \
  tests.test_v02_v03_causal_counterfactual_soak \
  tests.test_v08_street_notice_booklet_loop \
  tests.test_v08_courtyard_drying_loop \
  tests.test_v08_health_candidate_policy -v
```

结果：`Ran 80 tests ... OK`（WSL，`223.076s`）。

## 7. 未改动项

- 不新增依赖，不修改任何对外 HTTP 契约或数据格式；
- 未触碰 `autonomous_life_assembly.py`、`life_sharing_service.py`、
  `readonly_recall_adapter.py`；
- 未调用真实产品 LLM，未执行 `git commit` / `git push`。
