# v0.2 Autonomous Life｜WP-00 当前仓库与运行入口核查

**性质：施工前基线记录。本文只写"代码里实际存在什么"，不写设计意图，也不声称任何功能已上线。**
版本：2026-10-10。基线提交：`origin/main = 251e920891657e954b6f1d85d3ce6820bedba308`（与总纲记录一致）。
工作分支：`v0.2-autonomous-life`（从 `251e920` 建，工作副本 `D:\nyairo-v02`）。

> 注意：另一个本地副本 `D:\nyairo-site-work` 的 refs 与 GitHub 全线不一致（历史被重写过，main = `29b88fa`，且缺 `m0_writer_worker.py` 等发行线文件）。**不要用它当基线。** 本分支从 GitHub 的 `origin/main` 建。

---

## 1. 运行环境结论（影响后面所有 WP 的验证方式）

- `components/life/**` 依赖 POSIX `fcntl`（`life_integration_ag2.py:42`、`activity_continuity.py`）。**Windows 原生 Python 无法加载 Life Runtime**：实测 `python components/check_life.py` 失败于 `ModuleNotFoundError: No module named 'fcntl'`，Life 进入 `READ_ONLY_DEGRADED (RUNTIME_NOT_LOADED)`。
- 因此所有真实运行在 **WSL Ubuntu 2（Python 3.12.3）** 的隔离目录里做；Windows 侧只做编辑与 `git`。
- 仓库 `vendor/hermes` 是 vendored 目录（非 submodule，155 MB），当前**没有 `.venv`**，所以 `scripts/test.py` 的 Hermes 段跑不通；组件段（life / supply / world / native）可直接用 `unittest` 跑。
- `scripts/test.py:20` 用 `os.pathsep.join` 拼 `PYTHONPATH`，在 Windows 上分隔符错误（应为 `;`），该脚本是 Linux 专用。

## 2. 已有资产与真实入口（可复用清单）

| 领域 | 真实入口 | 位置 |
|---|---|---|
| Life 装配根 | `ProductionLifeRuntime` / `build_production_life_runtime()` | `components/life/scripts/life_runtime_production.py:254` / `:746` |
| 闭环编排 | `LifeIntegrationCoordinator.run_decision_and_adoption_cycle(...)` | `components/life/scripts/life_integration_ag2.py:4526` |
| 候选生成 AG-0 | `CandidateMaterializer.build_candidate_set(observed_at=)` | `candidate_sources_ag0.py:2719`（经 AG-2 端口暴露为 `:2822`） |
| 候选源协议 | `CandidateSourceAdapter`（`is_enabled/availability_status/discover/validate_source/materialize/refresh`） | `candidate_sources_ag0.py:1005` |
| 决策 AG-1 | `AgencyDecisionService.evaluate_opportunity(...)` | `agency_decision_ag1.py:3467` |
| 采纳 AG-2 | `DecisionAdoptionCoordinator.adopt_decision(...)` | `life_integration_ag2.py:2893` |
| 动作桥 | `DecisionActionBridge.create_step_and_propose_action(...)` | `life_integration_ag2.py:3644` |
| 单步执行 | `LifeIntegrationCoordinator.execute_activity_step(...)` | `life_integration_ag2.py:4851` |
| canonical Activity | `ActivityCommandService{start_activity,pause_activity,abandon_activity}` | 窄端口白名单 `life_runtime_production.py:538` |
| 作品 owner | `ManagedArtifactStore{create_artifact,read_artifact,read_head,read_version,commit_markdown,list_artifacts}` | `components/supply/lifesupply/artifact/store.py:193/247/264/274/298/497` |
| Supply 服务 | `LifeSupplyService`，Unix socket 单行 JSON，无 TCP | `components/supply/lifesupply/service/life_supply.py:398` / 分发 `:580` |
| Life 侧只读客户端样例 | `LifeSupplyReadClient` + `LifeSupplyCandidateSource` | `components/life/scripts/life_supply_candidate_source.py:91` / `:125` |
| World 唯一提交权威 | `WorldResolver.resolve(proposal)` | `components/world/scripts/world_action_resolver.py:634` |
| World 单写者服务 | `WorldBodyService`（admin/gateway 两个 socket，`SO_PEERCRED` 需 uid 0） | `components/world/service/world_body_service.py:87` / `:360` |
| 插件入口 | `plugins/chiyo/__init__.py:2`，含 `/chiyo_status` | `chiyo_bundle/hermes_plugin.py:96` |
| Life 插件装载 | `importlib.util.spec_from_file_location` 动态 exec `$CHIYO_NATIVE_LIFE_ROOT/plugin/life_runtime_adapter.py` | `components/native/native_life.py:66-83` |
| 每轮钩子 | `life_hook` / `_hook_impl` | `components/life/plugin/life_runtime_adapter.py:428` / `:441` |

### 2.1 状态存储形态（重要）

Life 链**完全没有 sqlite**：全部是 JSON 快照 + JSONL 日志 + flock 租约。

| 组件 | 文件 |
|---|---|
| AG-1 决策 | `agency_decision_state.json` / `agency_decision_journal.jsonl` |
| AG-2 集成 | `life_integration_state.json` / `life_integration_journal.jsonl` |
| LR-2 canonical Activity | `canonical_life_state.json` / `life_transitions.jsonl` |
| LR-5 进度 | `activity_progress_state.json` / `activity_progress_journal.jsonl` |
| AR-0 动作 | `action_reality_state.json` / `action_reality_journal.jsonl` |
| AR-1 结算 | `result_settlement_state.json` / `result_settlement_journal.jsonl` |
| 候选审计 | `candidate_audit_journal.jsonl` |

sqlite 只在 Life Supply 侧：`life_supply.sqlite`（表 `managed_artifacts / artifact_versions / artifact_operations / artifact_effect_receipts / permission_grants / workspaces / operation_keys` …），作品正文投影到 `<data_root>/projection/<sha256(artifact_id)[:2]>/<sha256(artifact_id)>.md`。

## 3. 现有闭环真实调用链（代码级）

```
run_decision_and_adoption_cycle(trigger_kind, trigger_ref, ...)   ag2:4526
  ├─ kill_switches.require_agency()                               ag2:4554
  ├─ 1. AG-0  build_candidate_set(observed_at)                    ag2:4560 → ag0:2719
  ├─ 2. AG-1  DecisionOpportunity.create → evaluate_opportunity    ag2:4572 / :4619
  ├─ 3. AG-2  adopt_decision(...)                                 ag2:4676
  │     └─ 真实 canonical 写：activity_command.start_activity      ag2:3139
  ├─ 4. 单步：execute_activity_step(step_spec_input)               ag2:4828 → :4851
  │     ├─ DecisionActionBridge.create_step_and_propose_action      ag2:4868 → :3644
  │     ├─ kill_switches.require_action_execution()                ag2:4916
  │     └─ submit_step_action_and_apply_post_submit_behavior        ag2:4921 → :3792
  └─ 5. AR-1 settle + LR-5 传播 + ExperienceHandoff                ag2:4976 → :4988
```

- 决策枚举（AG-1，8 个）：`START / CONTINUE / PAUSE / WAIT / RESUME / ABANDON / DEFER / NO_ACTION`（`agency_decision_ag1.py:178-198`）。**没有 `DO`**；总纲的 `DO` 对应现有 `START`（或对进行中活动的 `CONTINUE`）。
- 合法触发（8 个）：`CANDIDATE_SET_CHANGED / USER_REQUEST_EVENT / RESUME_ELIGIBILITY_OPENED / CURRENT_ACTIVITY_ENDED / CURRENT_ACTIVITY_RECONSIDERATION / WORLD_OPPORTUNITY_EVENT / EXPLICIT_REEVALUATION / RECOVERY_RECONCILIATION`（`agency_decision_ag1.py:220-240`）。
- **定时器被显式禁止**：`PERIODIC_LLM_TICK / CRON_TICK / IDLE_TIMER_POLL` 在 `FORBIDDEN_OPPORTUNITY_TRIGGERS`（`agency_decision_ag1.py:242`），构造 opportunity 时直接抛错（`:967`、`:1024`）。→ WP-01 的"空闲机会窗口"必须走 `EXPLICIT_REEVALUATION`（或真实事件触发），**不能**做成 tick。

## 4. 为什么 Agency Shadow 没有写 canonical Activity（WP-00 必答项）

三个原因叠加，缺一不可：

1. **Shadow 模式根本不装配 runtime。** `life_runtime_adapter.py:482-483`：shadow 下 "no lease, no store, no writes"，`runtime_loaded_in_gateway: False`。
2. **即使 live 模式，turn 路径也刻意不碰 canonical。** `life_runtime_adapter.py:510-514` 注释写明 "the canonical EFFECT path is deliberately NOT invoked here … the turn path reaches no canonical Decision writer at all"，随后只调 `wiring.on_turn`（`:540`）。
3. **Shadow 是第二个独立决策服务，对象图里没有 AG-2。** `agency_cognition_worker.py:197` 用 `store_root=<install>/shadow-state`、`namespace="shadow"`；`:357 observe_turn` → `:395 build_candidate_set` → `:570 evaluate_opportunity`，**整条路径从不调用 `adopt_decision`**。全仓 `adopt_decision` 的调用点只有 `ag2:4676` 一处，而它只在 `run_decision_and_adoption_cycle` 内部。

再加上自检门钉死：`life_cognition_wiring.py:175-188` 一旦发现 `runtime.revision() != 0` 或 `canonical_decision_written / adoptions_created / activity_mutations != 0` 就判定 degrade OFF。

**结论：** 写 canonical Activity 的唯一代码路径是 `ag2:4676 → ag2:3139 → CanonicalActivityStore`，而仓库里唯一会走到它的是 `components/alpha/chiyo/cli.py:400`（`IsolatedLifeRuntimeHarness` 上的诊断 CLI）。生产插件路径**结构上到不了**这一行。WP-01 要补的就是这个"到得了"的真实入口。

## 5. v0.2 必须补的缺口（当前不存在）

| 缺口 | 现状证据 | 归属 WP |
|---|---|---|
| 生产侧没有任何代码调用 `run_decision_and_adoption_cycle` | 全仓唯一调用点 `components/alpha/chiyo/cli.py:400`（隔离 harness） | WP-01 |
| 没有任何 tick / run_once / 自主轮次入口 | `components/life` 下 grep `def .*tick\|run_once\|def cycle` = 0 命中 | WP-01 |
| 供 Life 主体调用的受限 ManagedArtifact 写端口**不存在** | grep `WritePort\|artifact_write\|ManagedArtifactPort` 在 `components/supply` 下 0 命中；`ports/` 只有 `governance_client.py` 且仅 legacy 用 | WP-03 |
| Life 侧只能"新建一次"作品，不能追加版本 | `chiyo_bundle/presence.py:103 save_note` 固定 `op="create_artifact"`，无 `commit_markdown` 路径 | WP-03 |
| 作品无"历史版本列表"与"差异"接口 | `artifact/store.py` 无 `list_versions` / `diff`；版本号是 UUID 而非内容寻址 | WP-03 |
| 所有外向适配器 fail-closed，执行路径打不开 | `RefusingWorldResolver/RefusingToolExecutor/RefusingMessageProvider/RefusingFileAdapter`（`life_runtime_production.py:112/121/126/139`）；`DisabledCognitionAdapter`（`:167`） | WP-05 |
| World 写路径在生产装配里未接线 | `service/bridge/` 无 `__init__.py`，全仓无注册点；`chiyo_bundle/instance.py:70` 只设 `WORLD_BODY_INTEGRATION_MODE`，不设 `WORLD_BODY_MUTATION_MODE` / `WORLD_BODY_INTENT_MUTATION_GATE`（默认 off） | WP-07 |
| 发布包只跑 World **只读**包装 | `chiyo_bundle/world_read_service.py:58`，只 bind `run/read.sock` 并只转发 `GLOBAL_READ_COMMANDS` | WP-07 |

## 6. 冲突与"文档 vs 实现"差别（施工时必须按代码走）

1. **总纲的 `DO` 不存在。** 现有 8 个决策枚举里没有 `DO`；对应语义是 `START` / `CONTINUE`。不新增枚举，避免造第二套状态机。
2. **总纲说"时间器给机会"，但 AG-1 禁止 tick 触发。** 两者可以并存：定时器只做"该评估了吗"的判断并落 `EXPLICIT_REEVALUATION` 触发，**不得**生成日记/目标/聊天。
3. **`ACTION_EXECUTION_ENABLED` 打开也执行不了。** 生产适配器是 `Refusing*`，会抛 `ProductionExecutionRefused`（`life_runtime_production.py:103`）。所以 WP-05 不是"改环境变量"，而是**真的把某个窄动作的适配器接上**。
4. **`Instance` 里没有 `*_ENABLED` 属性。** 那两个名字只是 env 写入（`chiyo_bundle/instance.py:91`）；`instance.py:177` 的 health 硬编码 false。不要去找属性。
5. **Supply 默认配置必然启动失败。** `DEFAULT_SOCKET_PATH` / `DEFAULT_DATA_ROOT` 是相对路径（`life_supply.py:81-82`），而 `ServiceSettings.validate()` 要求 socket 绝对路径（`:204`）。必须显式设 `LIFE_SUPPLY_SOCKET`。
6. **写作品必须先有 Governance grant + 已开通的 workspace + `ARTIFACT_EXTERNAL_ACTION=ON`。** 缺任一项返回 `BLOCKED`（`life_supply.py:997`、`:1078`；`switches.py:38`）。调用者身份来自 `SO_PEERCRED`，**不是** JSON 字段（`:381`、`:538`）；`COMMAND_OPS` 只允许 `KIND_SERVICE_PRINCIPAL`（`:243`）。
7. **`adopt_decision` 要求 DecisionRecord 已在 canonical store 且新鲜**，否则 `REJECTED_STALE`（`ag2:2977`）。
8. **两份同名副本容易引错。** `components/alpha/chiyo/life_runtime/*` 与 `components/life/scripts/*` 是同名不同内容的两个副本（例如 `current_activity.py` 3 KB vs 26 KB）。v0.2 一律以 `components/life/**` 为准。
9. `components/world/service/` 与 `service/bridge/` 存在重复实现（`world_action_authorization.py`、`action_intent.py` 各两份，时区还不一致：`+08:00` vs UTC）。WP-07 只挑一份接，不合并重构。

## 7. E00.2 运行链映射表（能力 / 入口 / caller / 开关 / 可调用 / 缺什么）

状态列在 2026-10-10 的隔离环境里**实测**过，不是读代码推断；`可实际调用` 指"在这个隔离环境里真的跑出过结果"。

| 能力 | 真正的入口 | 真实 caller / owner | 当前启用开关 | 运行模式 | 可实际调用？ | 缺什么 |
|---|---|---|---|---|---|---|
| Candidate（AG-0） | `CandidateMaterializer.build_candidate_set(observed_at=)`（`ag0:2719`）；源注册表在 `life_runtime_production.py:460` | `LifeIntegrationCoordinator.run_decision_and_adoption_cycle`（`ag2:4560`）；owner = 各 canonical 读口 | 无 per-source 开关；受 `LIFE_RUNTIME_ENABLED` + `AGENCY_ENABLED` 约束 | `production` 命名空间 | **YES** | `PERSONAL_OPPORTUNITY` 在 v0.1 没有真实源；v0.2 补 `artifact_continuation_source.py` 后才产出候选 |
| Decision（AG-1） | `AgencyDecisionService.evaluate_opportunity`（`ag1:3467`） | `ag2:4619`；owner = AG-1 自己（`AgencyDecisionStore`，JSON+JSONL） | `AGENCY_ENABLED`（`life_runtime_production.py:491` 按 kill switch 注入） | `production` 命名空间 | **YES**（0 候选 → 规则 `NO_ACTION`；有候选 → `COGNITIVE_REQUIRED`，需要认知适配器） | 生产装配里装的是 `DisabledCognitionAdapter`（`life_runtime_production.py:167`）；真实模型适配器从未接线 |
| Activity（LR-2） | `ActivityCommandService.{start_activity,pause_activity,abandon_activity}`，经 AG-2 窄端口（白名单 `life_runtime_production.py:538`） | `DecisionAdoptionCoordinator.adopt_decision`（`ag2:3139`）；owner = `CanonicalActivityStore`（`canonical_life_state.json` + `life_transitions.jsonl`） | 无独立开关；需 `ActivityAuthorityLease` + capability，`CHIYO_LIFE_PRODUCTION_CUTOVER=canonical` | `canonical` 命名空间 | **YES** | 无（v0.2 验收里 START/CONTINUE 都真写了 canonical） |
| Action（AR-0） | `ActionCommandService.{propose_action,prepare_action,submit_action}`（`action_reality_ledger.py:2675` 等） | `DecisionActionBridge.create_step_and_propose_action`（`ag2:3730`）/ `submit_step_action_and_apply_post_submit_behavior`（`ag2:3804`）；owner = `ActionStore`（`action_reality_state.json`） | `ACTION_EXECUTION_ENABLED`（`ag2:4916`）+ `SandboxExecutorCapability` | `production` 命名空间 | **YES**（仅作品窄动词，v0.2 接了真实适配器） | World/Tool/Message 三个适配器仍是 `Refusing*`；`FILE_ACTION` 在 v0.1 只有沙箱实现 |
| Supply commit | `LifeSupplyService.dispatch` 的 `commit_markdown` / `create_artifact`（`life_supply.py:1077` / `:996`） | v0.2 窄端口 `LifeArtifactPort`（`life_artifact_port.py`）经 Unix socket，身份 = `SERVICE_PRINCIPAL`（`SO_PEERCRED`） | `ARTIFACT_EXTERNAL_ACTION=ON` + 一份 REUSABLE 的 `COMMIT_MANAGED_ARTIFACT` grant（target `workspace:<id>`） | 独立进程 + 单库 `life_supply.sqlite` | **YES** | v0.1 的 Life 侧只有 `chiyo_bundle/presence.py:103 save_note`，且固定 `create_artifact`、无法追加版本 |
| Memory admission | 无真实入口；生产装配装的是 `RefusingSettlementConsumer("memory")`（`life_runtime_production.py:156`） | `ResultSettlementService.register_consumer`（`ag2:445`）会调用它 | 无 | — | **NO** | 需要一条只接受合法行动经历的最窄 handoff（E07）；当前不存在 |
| World（写） | `WorldResolver.resolve(proposal)`（`world_action_resolver.py:634`）→ `WorldBodyService.act`（`world_body_service.py:360`） | 仓库内只有 `service/bridge/intent_executor.py:279` 一处真实提交点，且该 bridge **没有 `__init__.py`、全仓无注册点** | `WORLD_BODY_INTENT_MUTATION_GATE`（默认 off）、`WORLD_BODY_MUTATION_MODE`（默认未设） | — | **NO**（生产里只跑只读包装 `chiyo_bundle/world_read_service.py`） | 需要一条合法且被装配的 World action port（E08）；`chiyo_bundle/instance.py:70` 只设了 `WORLD_BODY_INTEGRATION_MODE` |

**E00 结论**：第一条 `commit_markdown` 生活路径的每一段现在都有真实 caller/owner（Candidate → Decision → Activity → Action → Supply commit），且都在隔离环境里真跑出过结果；断点只有两处：**认知适配器**（v0.1 装的是 Disabled，B4 要接真实模型）与 **Memory admission / World 写口**（E07/E08 才做）。

## 7. 本阶段不做（截断范围）

不做全仓 SHA256 巡检、不重写 Hermes/Native Memory/World/Supply、不新增 Authority Domain、不为 v0.2 建通用迁移平台、不做 3D/主动外发/付款/任意 shell。
