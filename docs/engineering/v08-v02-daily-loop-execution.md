# v0.8 Wave 6 · 0.2（v2.0）日常循环全量候选执行

本文件说明 0.2（v2.0）七类日常线索如何进入 v0.2/v0.3 自主日常循环：候选由
`collect_v02_v03_loop_candidates` 生成，`AutonomousLifeCycle` / `AutonomousLifeResident`
选中后由 `v02_v03_daily_loop_bridge` 构造确定性事件并经既有 owner 结算。桥接层不做
产品 LLM 调用（`llm_calls == 0`），不新增依赖，不改任何对外 HTTP / 数据格式。

## 1. 上下文

Wave 5 已打通 0.3（照护 / 发呆 / 收入 / 购买 / 亲密断点）的真执行闭环。0.2 的七类
候选虽然早就能生成描述，但 `_v02v03_event_from_descriptor` 只识别 0.3 的 ref 前缀，
`settle_v02_v03_round_consequences` 也没有对应落地，且 `AutonomousLifeResident`
的 `target_refs` 仍按 `v04_` / `v03_` / `purchase_cue:` / `market_wish:` 白名单过滤。
结果是这七类候选能被看到、能被选中，却无法真正推进状态。

本批交付：

- 事件构造：`_v02v03_event_from_descriptor` 按 producer 自己的 `action_family` 识别七类；
- 结算：`settle_v02_v03_round_consequences` 增加一个按 `action_kind` 分发的
  结算映射表，七类各自调用真实 owner；
- Resident：取消 `target_refs` 前缀白名单，统一交给桥接事件解析器判断可执行性；
- Cycle：补一处最小兼容，让不带 `fitem:v02v03-` 前缀的普通明日待办（`fitem:`）也能
  命中 `V02V03_DAILY`，同时保持“带 artifact 的明日便签继续走真实作品续作”不变；
- 测试与文档：`tests/test_v08_v02_daily_loop_execution.py` 覆盖七类 + 两条病中拒止 +
  跨 Decision 幂等 + 真实 Cycle/Resident seam。

## 2. 七类 0.2 候选

| action_family | source_ref | 事件 `action_kind` | 结算 owner 与真实落地 |
|---|---|---|---|
| `follow_tomorrow_note` | `tomorrow_note:<note_id>` 或 `fitem:<id>` | `FOLLOW_TOMORROW_NOTE` | `desk_tomorrow_note.json` → `DONE`；`FutureItems.close(reason=FOLLOWED_UP_APPLIED)` |
| `adopt_tentative_plan` | `plan:<plan_id>` | `ADOPT_TENTATIVE_PLAN` | `home_hearth_view.drop_plan` 只收起命中的那一条；收据记录 `resolution=ADOPTED` |
| `advance_workbench_draft` | `artifact:*` / `tray:*` | `ADVANCE_WORKBENCH` | `workbench_curio_view.set_tray` 更新托盘 note，保留原 `work` 与 baseline |
| `follow_bookstore_bookmark` | `book:<book_code>` | `FOLLOW_BOOKSTORE_BOOKMARK` | `outing_bench_bookstore.json` 的书签 note / `updated_at` / revision 推进 |
| `strenuous_outing_walk` | `route:<route_id>` | `STRENUOUS_OUTING_WALK` | `apply_save_walk_route` 更新路线 caption；`apply_log_bench_visit` 记录首站到访 |
| `observe_plant_journal` | `plant:<plant_id>` | `OBSERVE_PLANT_JOURNAL` | `long_companionship_view.apply_link_plant_sketch` 追加观察；revision 递增 |
| `clarify_common_ground` | `common_ground:<item_id>` | `CLARIFY_COMMON_GROUND` | `v02_common_ground.mark_shared_with_partner`；待澄清队列减少 |

事件只携带描述符事实（标题、plan/route/book/plant/cg id、strenuous 标记、托盘 note 等），
不携带挂钟时间。因此 Cycle 与 Resident 对同一 Decision 构造出的字节级事件一致，
consequence ledger 的 operation key 与 intent hash 也一致。

### 2.1 operation key 与幂等

七类统一使用 `_v02v03_operation_key(raw_ref, decision_id)`：

- 同一 `decision_id` 重放 → 命中 ledger，返回缓存结果 + `already_applied=True`，不二次写；
- 不同 `decision_id` → 新的逻辑动作：工作台 / 书签 / 路线 / 植物会继续推进；
- 一次性动作（明日便签、共同认知澄清、试探计划）在目标已关闭时是安全 no-op：
  仍返回 `APPLIED`，details 里标记 `already_closed` / `already_clarified` /
  `already_resolved`，并写入本次 Decision 自己的幂等收据，不重复报错、不重复改状态。

## 3. 身体门控

`adopt_tentative_plan`（`strenuous_outing=True`）和 `strenuous_outing_walk` 在结算时
重新读取 `_read_ecology_envelope_zero_write` 的 `capability_envelope`：

- `blocks_strenuous_outing=True` → `REFUSED`，`reason_code=ILLNESS_BLOCKS_STRENUOUS_OUTING`，
  `optimistic_writes=0`，不改计划、不改路线、不写 ledger；
- 采集侧原有的 Gate 3 仍然生效，结算侧是第二道硬门。

## 4. Resident 与 Cycle 的最小改动

- `autonomous_life_resident.py`：`_run_v02_v03_bridge_hook` 不再按前缀过滤
  `target_refs`，按 `origin_source_ref` → `target_refs` → `source_ref` 顺序交给
  `v02_v03_round_event_for_selected_candidate`。事件构造器是“是否可执行”的唯一权威。
- `autonomous_life_cycle.py`：`_v02_v03_descriptor_for_decision` 保留
  `fitem:v02v03-` 主门；额外允许 `fitem:` 且 **不含 artifact target** 的普通明日待办
  进入 `V02V03_DAILY`。带 artifact 的明日便签继续走既有“续作真实作品”路径，行为不变。

## 5. 明确不做 / 已知边界

1. **不发明 ADOPTED / EXECUTED 计划状态。** `home_hearth_view` 的既有契约是
   “只能提 / 改期 / 放下，不造 ACCEPTED”。本批用 owner 自己的终止动作 `drop_plan`
   收起被采纳的试探计划，并在结算收据中记录 `resolution=ADOPTED` /
   `adopted_at` / `decision_id` / `operation_key`。修改计划状态词表属于契约变更，
   未获授权不做。
2. **不为散步伪造 illness strain 事件。** v0.3 ecology 的 `STRAIN_REST_EVENTS` 只有
   淋雨 / 熬夜 / 漏餐 / 卧床休息 / 温水，没有“远足”。本批把真实体力活动记在
   `outing_routes_market` 的路线 caption 与 `outing_bench_bookstore` 的首站到访记录，
   不把远足假装成疾病 strain。
3. **植物手账只写规范路径。** 采集兼容旧的 `longterm_first_room_habits.json` 别名，
   但结算走 `longterm_room_habits.json`；只有别名、没有规范文件的实例会得到命名拒止
   `PLANT_JOURNAL_STATE_MISSING`，不会凭空新建第二份手账。
4. **工作台不生成内容、不伪造版本。** 只推进托盘 note；`work.artifact_id` /
   `baseline_version_id` 原样保留，作品版本只能由真实创作链路提交。

## 6. 验证

WSL `/tmp/rc104-venv`、0 产品 LLM 调用：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest \
  tests.test_v08_v02_daily_loop_execution \
  tests.test_v08_v03_daily_loop_execution \
  tests.test_v02_v03_causal_counterfactual_soak \
  tests.test_v08_street_notice_booklet_loop \
  tests.test_v08_health_candidate_policy -v
# Ran 95 tests ... OK
```

`tests.test_v02_deep_water_closure` 在本工作树中不存在（`tests/` 下无 deep_water
文件），无法纳入；其余五套全绿。冻结锚点 SHA-256 校验（soak test 01）通过，
`autonomous_life_assembly.py` / `life_sharing_service.py` 未改动。
