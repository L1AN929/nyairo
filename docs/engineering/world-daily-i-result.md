# I 包回执：世界日常（真实机会 → 消费/出行 → owner 回执）

- 工作区：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），branch `work/v03-w01-navigation`，
  HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）。
- 状态：**UNCOMMITTED**。未 stage / commit / push / reset / clean / checkout；W/F 与 A/B/C/D 的未提交改动原样保留。
- 范围：只碰本包独占写集 + 三处**最小 verb 白名单登记**（见 §2，父级派工明确授权）。未碰
  `world_living_skeleton.py`、`world_daily_opportunities.py`、`world_consumable_contract.py`、
  `world_body_substrate.py`、`world_body_gateway_api.py`、AG-0/AG-1/AG-2 权限语义、Memory/Supply/Body owner、看板、他人笔记。
- 未做：真实产品模型、外网、真实支付/余额/购买、生产部署/profile/凭据/provider、24h、删除任何文件、commit/push、子委派。
- 时钟：本包演练用**显式受控 aware clock**（注入 `now` / 目录 `now` / 旅程 `observed_at`）——报 `INJECTED_CLOCK`，
  不是 real-clock、不是 24h。世界服务自己的 clock 仍是真实墙钟（见 §7 的 W 用例漂移说明）。

---

## 1. owned delta（只列本包改动的文件与位置）

| 文件 | 改动（当前行号） |
|---|---|
| `components/life/scripts/world_move_opportunity_source.py` | 新 `CONSUME_VERB`(62)/`PLACE_REF_PREFIX`/`OBJECT_REF_PREFIX`(66-67)；`_validity_window`(80)；`observed_move_opportunities` 语义不变（88）；新 `observed_consume_opportunities`(140，只读 gateway 的 `get_available_actions` 的 CONSUME 条目，`available is not True` 恒空）；新 `read_location_catalog`(198，只读 W 目录、失败即 `unavailable`）；新 `opportunity_view`(227，带 kind/verb/source/availability/uncertainty/walk_seconds，`action_grant:false`）；provider 增 `last_available_actions`/`last_catalog`/`last_opportunity_view`/`_moment()`（目录必须收 aware `datetime`，ISO 只做 id/有效期） |
| `components/life/scripts/autonomous_life_cycle.py` | 常量 `PLACE_REF_PREFIX`/`WORLD_OBJECT_REF_PREFIX`/`WORLD_CONSUME_FACT_KEY`/`CONSUME_VERB`(130-134)；`last_world_opportunity_view`/`last_world_catalog`(724-725)；`materials()` 上报 `world_opportunity_view`/`world_catalog`(1530-1549)；`_refresh_world_opportunities` 收下 provider 的并行视图(2087-2091)；`_chosen_target_ref`(2094) + `_chosen_place`(2113)；`_world_adoption_spec` 按 target 命名空间分流(2160-2165) + 新 `_consume_adoption_spec`(2207)；`_candidate_is_world` 认 `place:`/`object:`(2262)；`_world_step_spec` 按命名空间产出 MOVE / CONSUME 步骤(2297)；`_classify_world_step` 按 payload verb 分流(2597) + 新 `_classify_world_consume`(2653)；新 `_record_world_consume_progress`(2740) |
| `components/life/scripts/world_action_bridge_adapter.py` | `SUPPORTED_VERBS = ("MOVE","CONSUME")`(67)、`VERB_PARAMETER` 加 `CONSUME → object_id`(72)；`_success` 对 CONSUME 追加 `details`（`_consumption_details`(363)：读 owner 的 `get_action_result`，给 `receipt_present`/`consumption_receipt`，读不到就如实说读不到） |
| `components/life/scripts/world_journey_action_adapter.py` | 新 `DELEGATED_VERBS = ("CONSUME",)`(69) 与 `delegate`(166)：非旅程 verb 原样转 `WorldBridgeResolver`（正式链），`execution_id` 仍由本 resolver 派生；`describe()` 报 delegated；`query_status_for_reconcile` 对委托 verb 改问 owner 执行结果（按 verb 分流） |
| `tests/test_world_daily_life_consume.py`（新） | 6 个精确用例（不继承任何既有 TestCase，避免误收旧用例）：竞争机会+来源/有效期、真实消费全链、MOVE 仍计时、owner 不可用不翻成机会、owner 改判定→候选消失、安静 0 调用 |
| `docs/engineering/world-daily-i-result.md`、`world-daily-i-runbook.md`（新） | 本回执与运行说明 |
| `.agents/notes/implemented/architecture/2026-10-17-world-daily-life-consume-owner-path.md`、`.agents/notes/implemented/feature/2026-10-17-world-daily-life-opportunity-routing.md`（新） | 本包独有决策笔记 |

## 2. 写集外的最小登记（父级派工授权，理由如下）

F 的 `CONSUME` 在 Resolver/gateway 层是真的，但**从 Life 决策出发走不到**：三处 verb 白名单都没登记它。
不登记就无法"由一次真实 Decision 触发消费"，只能测试直调 resolver——那正是派工禁止的旁路。因此只做登记，
不动任何权限语义：

| 文件 | 改动 | 为什么必须 |
|---|---|---|
| `components/life/scripts/action_reality_ledger.py`(1762) | `WorldActionAdapter.validate_prepare` 的 verb 集合 `{"MOVE","POSE"}` → 加 `"CONSUME"`；payload 键仍只有 `verb/target/expected_dependencies` | 不加就在 AR-0 合同层被拒（`unsupported WORLD_ACTION verb='CONSUME'`），提交栅栏都到不了 |
| `components/world/service/action_intent.py`、`components/world/service/bridge/action_intent.py`(65/69) | `EXPOSED_VERBS` / `ACTION_ALLOWLIST` 加 `"CONSUME"`（两份同内容副本同步） | `build_submission` 对不在清单里的 verb 一律 `INVALID`；"加进 Resolver ≠ 获得许可"是它写下的规矩 |
| `components/world/service/test_action_intent.py`(149) | 那条"清单恰好是这四个"的断言同步更新并显式断言 CONSUME 在列；DROP/THROW/GIVE/… 负控原样保留 | 登记后旧断言必然红，不改就是"用假断言掩盖产品事实" |

未动：`intent_executor.py`（它自己没有 verb 清单，走 `action_intent.build_submission`）、
`world_action_authorization.py`（一次性授权本来按 verb+args digest 绑定，CONSUME 直接复用）、
AG-0/AG-1/AG-2 权限与单前台语义、`world_body_gateway_api.py`、`world_consumable_contract.py`。

## 3. 已实现（有本包实跑证据）

1. **机会翻译**：一次真实观察同时产出两类机会——`place:<邻居>`（来自 selector 可见 action space，机会集 = World 此刻给出的可选集）
   与 `object:<instance>`（来自 gateway 自己的 `CONSUME` 可用性投影，owner 说不可用就一个都不产出）；
   每条带 `subject_id`/`perception_ref`/`world_revision_ref`/`valid_from`/`valid_until`/`location_ref`，
   并行视图另带 `source`/`availability`/`availability_reason`/`walk_seconds`/`action_grant:false`；W 目录真的被读（`schedule_source=world_package_schedule`）。
2. **消费全链**：AG-1 选中 CONSUME 候选 → AG-2 Adoption(`APPLIED`) → AR-0（fence/attempt/receipt/reconcile）
   → Intent Executor 铸一次性授权 → `gateway.sock` → Resolver → 同一原子保存里 placement 消失 + 回执落盘
   → Body 恰好一条后果 → LR-5 收到 owner 回执带来的世界事实（`COMPLETION_ELIGIBLE`），Activity 真的结束并释放前台。
3. **消费 ≠ 到达**：消费轮 `journey` 字段不出现、位置不变；MOVE 轮仍是 `IN_TRANSIT`（出发 ≠ 到达），两条完成判据各自独立。
4. **不复活**：吃完后再观察，同一 instance 不再出现（候选空、placement 无、回执仍恰好一条）。
5. **安静**：没有任何真实材料时 `NO_MATERIAL`、0 模型调用、0 收据、0 后果。

## 4. 一次连续演练（同一实例，真实本地 owner，逐步 owner 身份与真实后果）

用例：`tests.test_world_daily_life_consume.DailyWorldConsumeTest.test_a_consume_decision_really_uses_one_instance_through_the_owner_gateway`
（WSL `w04venv/bin/python`，`PYTHONDONTWRITEBYTECODE=1`，隔离 home 留在 `/tmp/nyaw05r.jdK3q/i-daily-*` 未清理）。

| 步 | 谁做的 | 真实后果（owner 侧可回读） |
|---|---|---|
| 0 夹具初始化 | 真实 Supply owner（socket 服务）+ `worldgen.init_instance`（真实 World 包 `examples/worlds/daily_neighborhood_v1`） | 实例在 `home/bedroom`；`state/world_object_capabilities.json#consumables` 声明 `i-food-riceball-01`；基座 placement 在 `home` |
| 1 观察 | `WorldMoveOpportunityProvider` → 真 `WorldBodyClient`（真 `gateway.sock`） | 机会：`wopp:move:home:r1:street_front`(90s, OPEN/ALWAYS_AVAILABLE) + `wopp:consume:home:r1:i-food-riceball-01`(OPEN)；W 目录 `available/world_package_schedule` |
| 2 决策 | AG-0 来源门 → AG-1（确定性替身只从合法候选里挑 `object:` 那条） | Decision `START`，`selected_candidate_ref` 指向 CONSUME 候选 |
| 3 采纳 | AG-2 | `activity_kind=CONSUME_OBSERVED_OBJECT`，`adoption_status=APPLIED`，Activity `actv_*` |
| 4 执行 | AR-0 → Intent Executor（真授权注册表）→ gateway → Resolver（唯一提交者） | `world_verb=CONSUME`，`action_status=SUCCEEDED`，placement 从 substrate 消失 |
| 5 回执 | World substrate（与删除同一次原子保存） | `consumptions` 恰好 1 条：`object_id=i-food-riceball-01`、`body_id=chiyo_body`、`placement_before.location_id=home`，`execution_id` 与 AR-0 那次执行一致 |
| 6 Body | `body_consequence`（既有 2.0s light `PHYSICAL_RESULT`） | 该 execution 恰好 1 条后果，无 nutrition/satiety/hunger/health/recovery 声明 |
| 7 LR-5 | `_record_world_consume_progress` → `ag2_progress_port` | `progress_state=COMPLETION_ELIGIBLE`、无 `progress_ingest_error`；`canonical_state.foreground_activity_ref=null`（前台真的释放） |
| 8 再观察 | 真 gateway | 不再有 `object:` 机会；回执仍 1 条；后果条数不变 |

同文件另有：`test_a_move_decision_still_departs_without_arriving`（同一实例选地点 → `IN_TRANSIT`，位置不变、无消费），
`test_a_quiet_instance_makes_no_model_call`（0 调用）。

**未覆盖**：游玩/休息（非产出停留）的诚实起止、闭店改计划、回家恢复原私人保存点并续步、
真实经历进入随后输入的**实际作用**（M0 准入后改变下一步）。这几步本包**没有**做完，见 §8。

## 5. 命令与真实 exit

venv：`/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`，
一律 `PYTHONDONTWRITEBYTECODE=1`、`TMPDIR=/tmp/nyaw05r.jdK3q`、repo cwd，用 scratch runner
`i_daily_runner.py`（**只**把 `shutil.rmtree` 换成 no-op 以保留夹具；断言一字未改）。

| 命令 | exit | 结果 |
|---|---|---|
| `i_daily_runner.py tests.test_world_daily_life_consume` | **0** | `Ran 6 … OK` |
| `i_daily_runner.py tests.test_one_day_life` | **0** | `Ran 17 … OK` |
| `i_daily_runner.py tests.test_life_v1_integration` | **0** | `Ran 56 … OK`（455s） |
| `i_daily_runner.py tests.test_one_day_life_w05r tests.test_world_journey tests.test_life_continuity_inputs tests.test_life_situation_view` | **1** | `Ran 179`，1 failure：`RequestBudgetTest.test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset` —— 见 §7.2（与本包无关的墙钟依赖） |
| `i_daily_runner.py tests.test_life_v1_restart tests.test_life_v1_appraisal_context tests.test_world_move_tool tests.test_world_move_tool_navigation tests.test_world_move_pause_race tests.test_world_runner tests.test_body_capacity_source tests.test_short_appraisal_view tests.test_private_daily_items` | **1** | `Ran 184`，1 failure：`RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity` —— 见 §7.3（哈希种子依赖，单跑 exit 0） |
| `cd components/world/service && PYTHONPATH=.:../scripts … i_daily_runner.py test_object_action_m15 test_world_move_candidates test_world_object_capability test_world_body_service test_world_body_bridge test_world_action_authorization test_authorization_hardening test_world_action_lifecycle` | **0** | `Ran 155 … OK` |
| `i_daily_runner.py components.world.service.test_action_intent` | **0** | `Ran 24 … OK` |
| `i_daily_runner.py tests.test_world_consume tests.test_world_consume_faults tests.test_world_daily_opportunities` | **1** | 2 个 F 模块全绿；W 模块 1 failure —— 见 §7.1（W 自己的墙钟漂移） |
| `node --experimental-strip-types …/verify-agent-note-tree.ts` | **0** | `ok: 18 note(s) tree and relative links verified` |
| `node --experimental-strip-types …/verify-agent-note-format.ts` | **0** | `ok: 18 note(s) verified` |
| `git diff --check` | **0** | 无空白错误 |
| `git status --porcelain` / `git rev-parse HEAD` | 0 | HEAD 仍 `7f09d26d…`；cached 为空；本包文件全部 UNCOMMITTED |

日志：scratch `i-daily-life-v1-integration.log`、`i-daily-reg-a.log`、`i-daily-reg-b.log`、`i-daily-reg-c.log`、
`i-daily-reg-d.log`、`i-daily-restart-1.log`、`i_seed_probe.sh`。

## 6. 负例、权限与恢复（本包新增）

- **owner 判定为准**：把声明改成 `consumable:false` → 翻译层一个 `object:` 机会都不产出（`test_owner_verdict_really_governs_the_offered_object`）。
- **不可用不翻成机会**：`available:false`（`LOCATION_CLOSED`）与"旧网关没有 CONSUME 条目"两种输入都恒空；
  不可读的 W 目录只报 `unavailable`，不变成营业承诺（`test_an_unavailable_consume_entry_is_not_turned_into_an_opportunity`）。
- **权限**：消费只走一次性授权链；本包没有新增任何 mint/旁路；未授权时 gateway 按既有 `NOT_EXPOSED` 语义拒绝（F 已有 20+6 用例，本包未重复）。
- **恢复**：AR-0 对同一 `idempotency_key` 复用同一次执行（key 里带 place/revision/instance），旅程侧的
  `reconcile_request` 与委托 verb 的 owner 执行结果查询各自分流；`world_journey_action_adapter` 的 MOVE 语义一字未改（W05R 既有用例全绿）。
- **不重开旧阶段**：R1c / BodyUNKNOWN 相关路径未改，未做"未正向许可的旧阶段重开"。

## 7. 本包发现的既有缺陷（**未顺手改**，供父级定点派 owner）

### 7.1 W：`tests/test_world_daily_opportunities.py::test_closed_door_is_allowed_in_timed_travel_too` 是墙钟依赖

- 现象：断言 `self.position(env=env) == "street_corner"` 得到 `"cafe"`。
- 根因（已用只读探针证实，`i_probe_w_flake.py`）：`world_living_skeleton.read_move_state` 会先调
  `world_travel_delivery.realize_due_travel(now=None)`，即用**真实墙钟**结算在途行程；用例注入的时钟是
  `2026-10-17 13:00 JST`（= `04:00Z`），咖啡店段的 `expected_arrival_at = 04:05:30Z`。真实 UTC 一旦越过
  `04:05:30Z`，这次"尚未到达"的行程就被判定到期并结算，位置变成 `cafe`。
- 证据：同一次运行里 owner 自己的 `world_state.json` 仍是 `street_corner`，而 `read_move_state` 返回 `cafe`
  （探针输出 `EXPECTED_ARRIVAL 2026-10-17T04:05:30Z` / `REAL_NOW_UTC 2026-10-17T05:20:21Z` / `RAW_STORED_PLACE street_corner` / `READ_MOVE_STATE cafe`）。
- 与本包无关：本包未改任何 W 文件（`world_living_skeleton.py` mtime 11:06、`tests/test_world_daily_opportunities.py` mtime 11:08，均早于本包开工）。
- 建议：owner 侧决定 `read_move_state` 是否该隐式结算（若保留，用例应显式传 `now=` 或断言用 owner 原始状态读）。

### 7.2 W05R：`RequestBudgetTest.test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset` 是墙钟依赖

- 现象：`REQUEST_BUDGET_UTC_DAY_LIMIT`，`utc_day: '2026-10-17'`、`spent 2 of 2`。
- 根因：用例把 `lrb._utc_day` patch 成字符串 `"2026-10-17"` 来模拟"跨日"，但当真实 UTC 日期**就是** 2026-10-17 时，
  这个 patch 是恒等变换，"新的一天"分支不可达，于是仍按当天上限拒绝。
- 与本包无关：本包未改 `life_request_budget.py`（mtime 10-16 20:19）与该测试（10-16 20:57）。

### 7.3 D-final：`test_life_v1_restart` 的负控是**哈希种子依赖**（同一个窗口有时 ADOPTION_REJECTED、有时 DECISION_FAILED）

- 现象：`control["outcome"] == "DECISION_FAILED"`，`detail='INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE'`；
  期望 `ADOPTION_REJECTED / REJECTED_STALE / CANNOT_START_WHILE_FOREGROUND_ACTIVE`。
- 复现（scratch `i_seed_probe.sh`，单跑同一个用例）：`PYTHONHASHSEED=1 → exit 0`、`=7 → 失败`、`=12345 → 失败`、`=987654 → exit 0`。
- 判断：该负控依赖 `StubCognition("artifact")` 从候选列表里"取第一个非世界候选再发 START"，
  而候选/合法决策的排序对哈希种子敏感；本包未改 AG-0 候选来源与 AG-1 合法决策计算
  （该 worker 用的是 `test_one_day_life.StubObservation`，它只产 MOVE 机会，本包翻译层新增的 CONSUME 分支在此不参与）。
  这也与父级记录的 `parent-final-four` 同类（"harness 选 START 而不是 CONTINUE"）。
- 建议：负控不要依赖候选顺序（显式挑一个 START 合法的候选，或直接断言 AG-1/AG-2 两层各自的拒绝码）。

## 8. 真未完成（不夸称）

1. **游玩/休息**：没有实现"非产出停留（公园看/坐、阅读角停留）"的诚实起止与真实时段证据。本包没有假发
   `REST`/`SIT` 动作，也没有把 time skip 当"玩过了"；这一条**未接**。
2. **闭店改计划**：`destination_availability=CLOSED` 的提示已经随 MOVE 结果回到 Life 侧（W 提供），但
   "遇闭店改计划/等开门/折返"的选择逻辑**未实现**。
3. **回家 + 原私人保存点续步**：回家（MOVE 回 `home`）与私人事项恢复各自的机制都在（D 包已验），但本包**没有**把
   "出门 → 回家 → 恢复原私人事项保存点并续步"串成一次演练。
4. **真实经历进入随后输入并实际作用**：消费的 owner 回执已经进了 LR-5 与轮次记录，但**没有**验证它经 M0 准入后
   真的改变下一步（这需要接上 Memory 准入并跑一次连续场景）。
5. **一次同实例的完整连续演练（出门前保存点 → ≥2 机会 → 真实出门 → 闭店换计划 → 游玩 → 可选消费 → 回家 → 续步）未完成**：
   本包交付的是这条链里"机会竞争 + 真实消费 + 计时出行 + 安静"这几段的真实证据（§4），不是整条链。
6. **`build_action_space` 仍不产 CONSUME 候选**：Life 侧读的是 gateway 的 `get_available_actions`；World 侧 selector 工具路径今天看不到消费。
7. **`MOVE` 机会仍是单跳**：多跳可达地点只在 W 目录里作为路线事实出现，未变成可选动作（理由见笔记 Alternatives）。
8. 真实产品模型、24h/真实时钟长跑、部署、多实例、真并发：均未做（授权外）。

## 9. 工作区状态（本包相关）

```
 M components/life/scripts/action_reality_ledger.py
 M components/life/scripts/autonomous_life_cycle.py
 M components/life/scripts/world_action_bridge_adapter.py
 M components/life/scripts/world_move_opportunity_source.py
 M components/world/service/action_intent.py
 M components/world/service/bridge/action_intent.py
 M components/world/service/test_action_intent.py
?? components/life/scripts/world_journey_action_adapter.py   （更早阶段创建，本包只在其上增改）
?? tests/test_world_daily_life_consume.py
?? docs/engineering/world-daily-i-result.md
?? docs/engineering/world-daily-i-runbook.md
?? .agents/notes/implemented/architecture/2026-10-17-world-daily-life-consume-owner-path.md
?? .agents/notes/implemented/feature/2026-10-17-world-daily-life-opportunity-routing.md
```

同仓其它改动（W/F/A/B/C/D 的）不是本包的，未触碰、未回滚；旧失败现场与旧回执原样保留，未改写。
