# D 包回执：统一生活入口、有限共同时间与整合（INT / COEXIST；CT05/06/13/17/20）

- 候选树：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`）
- branch：`work/v03-w01-navigation`（未移动）｜HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）
- **全部改动 UNCOMMITTED**：未 `reset`/`checkout`/`clean`/`recreate`/`stage`/`commit`/`push`；未改 A/B/C 回执、未改其他未提交改动
- 未做：真实产品模型、外网、外发、24h/长跑、部署/自启、profile/provider/凭据改动、删除用户数据、整仓复制、递归委派
- 平台：WSL Ubuntu 隔离 venv
  `/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`（3.12.3），
  `PYTHONDONTWRITEBYTECODE=1`，未改全局包；夹具根 `/tmp/nyaw05r.jdK3q/d-*` 唯一创建并**保留**（未 rm/unlink/rmtree）

---

## 1. 本包实际自有 delta（只这些文件）

| 文件 | 状态 | 改动（函数/行级） |
|---|---|---|
| `components/life/scripts/autonomous_life_cycle.py` | 修改（UNCOMMITTED） | 新增 `import life_situation_view as lsv`、`CONTINUITY_WATERMARK_FILENAME`；`__init__` 新增 `situation_source`/`continuity_source`/`work_artifact_id`/`continuity_state_root`；新增 `_compact_column`/`_compact_knowledge`、`continuity_path`/`read_continuity_watermark`/`_write_continuity_watermark`/`read_continuity`/`continuity_continue_object`/`selected_artifact_id`/`_read_resume_point`、`budget_column`/`observations`/`read_situation`/`capacity_kwargs`；`materials()` 加 C 读 + 显式作品选择；`evaluate_once()` **删除** round-budget→capacity 推导，改注入 A 的四列，并新增 `situation`/`capacity_kwargs`/`capacity_state`/`resource_capacity_state`/`execution_possible`/`artifact_selection`；`status()` 与 `autonomous_life_cycle_from_env` 同步 |
| `components/life/scripts/autonomous_life_assembly.py` | 修改 | 新增 `WORK_ARTIFACT_ENV`/`WORLD_HOME_ENV`/`resolve_work_artifact_id`/`_load_module_by_path`/`resolve_body_port`/`resolve_situation_source`；`assemble_autonomous_life` 新增 `situation_source`/`continuity_source`/`work_artifact_id` 参数并透传给 cycle，返回 `work_artifact_id`/`situation`/`continuity_wired`；`__all__` 同步 |
| `components/life/scripts/autonomous_life_resident.py` | 修改 | `_memory_ports()` 改为返回 `(consumer, prompt_view, port)`；新增 `_continuity_source(runtime)`（C 读口，绑 Memory owner + runtime 的 activity/agenda）；`start()` 把 `continuity_source` 传进装配；`summary()` 新增 `work_artifact_id`/`situation_wiring`/`continuity_wired`/`last_situation`/`last_continuity`/`work_selection`；新增模块级 `_compact_round_view` |
| `components/life/scripts/one_day_life.py` | 修改 | docstring 增补 D 语义与新动词；新增绑定常量与 Memory 常量并纳入 `OWNED_ENV_NAMES`；`prepare()` 新增 `reading_title`/`reading_content`/`create_private_items`/`memory_db`/`memory_controls_db`/`memory_subject`，创建阅读材料 artifact、按需创建 typed 私人文档、记录真实房间观察，并把三件绑定与 Memory 边界写进实例环境；新增 `_observe_room`/`ensure_private_items`/`_items_for_write`/`read_page`/`add_memo`/`save_reference`/`observe_room`/`link_item_to_room_object`/`private_view`/`reading_view`/`classify_state`/`_step_receipts`/`timeline`/`artifact_port`/`private_items_adapter`/`reading_pages`/`reading_source_id`；`status()` 新增 state_class/room/reading/private_items/situation/continuity；`_report_work()` 改为**显式绑定**（不再 `latest_resumable`）；CLI 新增 `timeline`/`read`/`note`/`room`/`invite` 与相应参数 |
| `components/life/scripts/life_co_presence.py` | **新增** | 有限共同时间最小协议 adapter（编解码 + 接线）：`classify`/`parse_invitation`/`parse_end`/`session_ref`/`FiniteCoPresence.observe/consider/begin/end`/`operator_action` |
| `tests/test_life_v1_integration.py` | **新增** | 26 项（含继承的 10 项 W05 回归）：私人生活 6 项 + 同一实例连续场景 1 项 + `CycleTest` 既有 10 项 |
| `docs/engineering/life-v1-d-result.md` | 新增（本文件） | 本回执 |
| `docs/engineering/life-v1-runbook.md` | 新增 | 一个入口的操作手册 |
| `docs/engineering/life-v1-capability-status.md` | 新增 | 能力已接/未接表 |

**未改**（明确记录）：`life_artifact_port.py`（现有 `read_artifact`/`read_head`/`resume_point`/`commit_version`/`resolve_operation` 够用，一字未动）、
`private_daily_items.py`、`life_situation_view.py`、`life_continuity_inputs.py`、`body_capacity_source.py`、`short_appraisal_view.py`、
`life_runtime_production.py`、`life_integration_ag2.py`、`autonomous_life_runner.py`、`autonomous_life_events.py`、
`docs/engineering/life-v1-a|b|c-result.md`、`private-daily-items-usage.md`；
`tests/test_one_day_life.py`、`tests/test_one_day_life_w05r.py` **断言一行未改**（保持原样即通过，见 §5）。

---

## 2. 每项需求的实际接点与 owner 证据

| 需求（施工单 §六/§五 D） | 实际接点 | 真实证据 |
|---|---|---|
| A：真实 Body 与预算分离，不再用预算冒充身体 | `cycle.read_situation()` → `lsv.LifeSituationView.build(now,budget,observations)`；`cycle.budget_column()` 用 `LifeRequestBudget.check()/view()`；`cycle.capacity_kwargs()` → `lsv.ag1_capacity_kwargs` → `run_decision_and_adoption_cycle(**kwargs)` | 轮次记录里 `situation.body` 与 `situation.resource_budget` 两列并存；`body` 列 JSON 里不出现 budget/quota/token/credit/allowance/exhausted（负控断言）；容量四键齐全且 body 不是 `AVAILABLE`；无 Body 缝时 body 列 = `UNKNOWN`（A 的 `BODY_SOURCE_NOT_INJECTED`），AG-1 走 `ALLOW_WITH_UNKNOWN` 软允许（未配 `BLOCK_ON_UNKNOWN`） |
| A：移除 round 预算→capacity 的冒充 | `autonomous_life_cycle.py` 原 770–779 的推导被删；`execution_possible`/`execution_possible_basis` 单独记录"调用方能不能执行" | 轮次记录里 `capacity_state` == `situation.body.state`（不再等于预算推导值） |
| 作品与私人事项/阅读材料不被 latest 误选 | `WORK_ARTIFACT_ENV`/`resolve_work_artifact_id` → `cycle.work_artifact_id` → `cycle._read_resume_point()`（有绑定就 `port.resume_point(id)`，`latest_resumable` 只在无绑定时兜底）；入口 `_report_work()` 同样 | 集成测试：先建私人事项文档（比作品更新），`status()["work"]["artifact_id"]` 仍是绑定的作品，`selected_by == "BOUND_WORK"`；轮次记录 `artifact_selection.selected_by == "BOUND_WORK"` |
| B：真的通过现有 `LifeArtifactPort` 读本地 source 内容 | `one_day_life.read_page()`：`port.read_artifact(reading_id)` 取 owner 的 head（version_id/number）+ content → 拆页 → `record_source_acquisition(receipt_ref, obtained={"pages":[n],"chars":k}, excerpt)` → `set_reading_position(position=n)` → 可选 `mark_unclear` | 测试断言 `out["version_id"] == 真实 read_head 的 version_id`；取得记录/位置/未懂各自成条；默认 `metadata_view` 里没有正文（`assertNotIn("窗外")`）；页码越界是 `PAGE_NOT_PRESENT` 拒绝；材料推进后**重新读一页**→旧书签 `needs_reread=True`；收藏不取全文/不置已读（`content_fetched=False, read=False`） |
| B：房间物件走真实 World 观察（位置权威在 World） | `one_day_life._observe_room()` 调 `world_living_skeleton.observe_world(hermes_home, environment={CHIYO_WORLD_HOME, CHIYO_WORLD_PACKAGE, ...}, now)`，只取 `local_environment.furniture`；`link_item_to_room_object()` 把它交给 `pdi.note_item_at_observed_object` | 房间读的 `source_ref == "world_living_skeleton.observe_world"`、`placement_authority == "world"`、`place_id` 来自 owner 自己的 `current_location.location_id`；**真实阻塞见 §6.2** |
| C：读提示真的进入后续对象/可执行输入 | `cycle.read_continuity()`（每次考虑前一次只读）→ `materials()["continuity"]`；`continuity_continue_object()` 只在 C 说 `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT` 且其准入 past 行带 `artifact` ref 时，把该 artifact 变成这一轮的继续对象（`artifact_selection.selected_by == "CONTINUITY_HISTORY"`），于是可执行输入（`artifact_id`/`expected_head`/operation key）随之改变 | 机制已接、并有定向断言面；**本包未用真实 M0 端到端演示**（§6.3 如实标注未验） |
| C：水位落在现有运行状态位置 | `cycle.read_continuity_watermark()`/`_write_continuity_watermark()` → `<life state root>/continuity_watermark.json`（原子写） | 只在 C 接线时生成；未接线时如实报 `SITUATION_SOURCE_NOT_WIRED`/`PAST_SOURCE_NOT_WIRED`，不伪造 |
| C：绑定三 ID 不同概念 | `resident._continuity_source()`：binding.subject = **Memory owner**（`NYA02_MEMORY_SUBJECT` 或 `chiyo`），past = `LifeExperiencePort`；activity/agenda 取本 runtime 受控对象；**不强制**与 Supply workspace subject 或 `chiyo.global` 相同 | 代码即证据；A 的 situation 视图用**canonical** subject（Supply subject 会直接被 `validate_subject_id` 拒绝——共同时间实现里真实撞到过，见 §6.4） |
| 统一入口 prepare/run/status/pause/resume/timeline | `one_day_life.py` 的 12 个动词：`prepare`/`preflight`/`run`/`status`/`pause`/`resume`/`report`/`timeline`/`read`/`note`/`room`/`invite`；run 仍走既有 resident（budget/run identity/单前台/暂停合同不变） | `status()["state"]` 给出 IDLE/WAITING/PAUSED/BLOCKED/RUNNING/STOPPED + `basis` + `source`；`timeline()` 从 `resident_journal.jsonl` + cycle `life_step_journal.jsonl` + `control.list_recent_actions` + `list_pending_unknown` 组装，缺口不补 |
| 安静段 0 模型、区别 idle/waiting/paused/blocked | `cycle.evaluate_once` 的 NO_MATERIAL 分支 0 调用；`classify_state` | 测试：安静轮 `NO_ACTION`（1 次决策请求）而 `writer.call_count == 0`；无 resident 记录 → STOPPED；暂停位 → PAUSED（source=life_pause.json） |
| 暂停 + 真实重启接续 | 集成测试在**同一数据根**上关掉旧 runtime、重开新 runtime + 新装配 | 新进程下一轮：暂停位仍生效（`PAUSED_BY_OPERATOR`，`llm_calls=0`，替身决策 0 次）；作品绑定、阅读位置与 head、run identity 全部接续；入口对第二个 writer 返回 `EXIT_LEASE_HELD` |
| 有限共同时间：普通消息只观察 | `life_co_presence.FiniteCoPresence.observe()` | 普通入站 → `OBSERVATION_ONLY`，`pause_requested=False`、`activity_started=False`、`owners_written=0`，暂停位未被写 |
| 有限共同时间：显式邀请走 LR-3 有限中断 | `consider()`（真实读：LR-3 life view / 暂停位 / 预算 / 前台状态）→ `begin()` → `interruption_coordinator.assess_events([EXPLICIT_HOLD_REQUEST], apply_immediate_pause=True)` | 实测：`decision=ACCEPT`，`activity_effect=INTERRUPT_NOW`，`pause_applied=true`，`applied_transition_ref` 非空（规范暂停经 consequence port 落盘） |
| 有限共同时间：单前台可中断恢复 | `_park_private_activity()` → LR-4 `enter_waiting_with_condition(condition_kind=EVENT_MATCH, waiting_on_ref=wait_dep:co_presence-<id>, release_foreground=True)`；随后 AG-1/AG-2 的 `run_decision_and_adoption_cycle` 创建有限共同活动 | 实测：私人活动真的进入 WAITING 保存点；随后 `decision=START`、`adoption_status=APPLIED`、`activity_ref=actv_02ae…`、`single_foreground=true`。**关键发现**：仅 LR-3 的 canonical pause **不**释放单前台，LR-2 仍拒第二个 START；是"WAIT + release_foreground"这一步让共同活动合法存在 |
| 有限共同时间：结束 + 回保存点 | `end()` 把显式结束事件喂给 LR-4 `evaluate_event` | 实测：结束事件满足私人 resume condition `rcond:e025…` → **新 eligibility** `relig:d6c043…`（OPEN、`llm_calls=0`、`auto_resumed=false`），会话 CLOSED；回私人保存点必须再经 AG-0→AG-1 Decision→AG-2 Adoption（本模块**不**自行 resume，也不写 receipt） |
| 不能用 operator 全局 pause 模拟社交 | `consider()` 在暂停位为真时 `DECLINE`（`OPERATOR_PAUSED`）；本模块从不写暂停位 | 测试：暂停时邀请被合法拒绝；`life_pause.read()` 在普通消息与邀请决策后仍为未暂停 |
| 原子/不可中断不得强切 | `consider()`：`atomic=True` 或 LR-3 life view `interruptibility == "ATOMIC"` → `DECLINE`（`FOREGROUND_ATOMIC_NOT_INTERRUPTIBLE`） | 分支实现 + `begin()` 在 `effect != INTERRUPT_NOW` 时 `NOT_INTERRUPTED` 且 `activity_started=false` |
| 不开第二前台 / 不改并发合同 | 共同活动是普通单前台 Activity；会话记录显式写 `single_foreground: true`、`true_concurrency: DEFERRED_BEYOND_CURRENT_CONTRACT` | 实测 adoption 返回 `single_foreground: true`；真并发**明确延期**，未伪造 |

---

## 3. 连续场景逐步结果（同一实例、同一夹具根，`tests/test_life_v1_integration.py::DUnifiedScenarioTest`）

夹具：真实 `LifeSupplyService`（governance+supply 两个 Unix socket、canonical SQLite）+ 真实 World home（`init_instance` 写 `world_state.json`）+ 真实 `ProductionLifeRuntime` + loopback 确定性替身决策/内容/观察；**没有**真实产品模型。

| 步 | 做了什么 | 结果 |
|---|---|---|
| 1 | `prepare` 一件作品 + 一份 3 页阅读材料 + 一处房间观察 | 作品/阅读材料/私人文档各自显式绑定且互不相同；房间读 `available`、`placement_authority=world`、`place_id=home`（**该世界未声明局部环境**，见 §6.2） |
| 2 | 处境/预算分列 | `body` 列与 `resource_budget` 列并存；body 列无预算词汇；容量四键齐全；`budget_column.source == "life_request_budget.check"`、`allowed=true` |
| 3 | 一次候选窗口（作品 + 已观察地点同时合格） | `outcome=ACTED`、`step_path=AR0_WORLD_ROUTED`、作品 head 真的换了新版本、`artifact_selection.selected_by=BOUND_WORK`、`situation.body.state` 与 AG-1 收到的 body 列一致 |
| 4 | 真读第 2 页 + 未懂；收藏另一材料不读；作品留真实版本 | `version_id` == owner head；取得/位置/未懂各自成条；收藏 `read=false, content_fetched=false`；`unread_count=0` |
| 5 | 普通消息 + 显式邀请 + 暂停时邀请 | 普通消息 `OBSERVATION_ONLY`（无 pause、无 Activity）；暂停时邀请 `DECLINE/OPERATOR_PAUSED` |
| 6 | 共同时间开始与结束 | 见 §2 表末四行实测：`INTERRUPT_NOW` → WAITING 保存点 → `START/APPLIED` 共同活动 → 结束事件 → 新 eligibility；joint Activity 的完成证据**未 ingest**（§6.5） |
| 7 | 单写者 + 同数据根重启 | 第二 writer 被拒 `EXIT_LEASE_HELD`；重启后暂停位生效且 0 模型调用；作品绑定/阅读位置/head/run identity 全部接续 |
| 8 | 安静段与 timeline | 时间线只含真实记录（本场景含 cycle 的 STEP 回执）；未跑 resident 时 `ticks` 为 `None`（不补造 WINDOW）；`not_claimed` 明说"缺口=停机，不是经历" |

现场路径（保留未清理）：夹具根 `/tmp/nyaw05r.jdK3q/d-regress-naeuzs0k/`（本次最终跑）及各次 `d-regress-*`/`d-room-*`/`d-import-*`；每个用例内部另有 `w05svc-*`/`w05inst-*`/`w05world-*`（既有夹具自己管理）。

---

## 4. 接口改变（给后续使用）

- `AutonomousLifeCycle(runtime, ..., situation_source=, continuity_source=, work_artifact_id=, continuity_state_root=)`；轮次记录新增键：`situation`、`capacity_kwargs`、`capacity_state`、`resource_capacity_state`、`execution_possible`、`execution_possible_basis`、`artifact_selection`、`material.continuity`。
- `assemble_autonomous_life(runtime, ..., situation_source=, continuity_source=, work_artifact_id=)`；返回新增 `work_artifact_id`/`situation`/`continuity_wired`；新公开函数 `resolve_situation_source`/`resolve_body_port`/`resolve_work_artifact_id`。
- `one_day_life.prepare(..., reading_title=, reading_content=, create_private_items=, memory_db=, memory_controls_db=, memory_subject=)`；实例文件新增 `work_artifact_id`/`reading_artifact_id`/`private_items_artifact_id`/`room_observation`/`private_items_title`；环境新增 `CHIYO_LIFE_WORK_ARTIFACT_ID`/`CHIYO_LIFE_READING_ARTIFACT_ID`/`CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID`/`NYA02_MEMORY_*`（都进 `OWNED_ENV_NAMES`）。
- `one_day_life` 新公开函数：`read_page`/`add_memo`/`save_reference`/`observe_room`/`link_item_to_room_object`/`private_view`/`reading_view`/`timeline`/`classify_state`/`ensure_private_items`/`reading_pages`/`reading_source_id`/`artifact_port`；常量 `PAGE_MARKER="%%PAGE%%"`、`STATE_*`。
- `life_co_presence.FiniteCoPresence(runtime, state_root=, subject_id=, home=, cycle=, canonical_subject=)`；`begin()`/`end()` 返回结构见模块 docstring；`session_ref(id) == "wait_dep:co_presence-<id>"`（LR-4 的词表前缀）。
- **未改**任何 owner 的 schema/白名单/权限/单前台/Decision+Adoption 合同/Memory schema。

---

## 5. 命令与实际 exit / 耗时

隔离 python：`$PY=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`，
一律 `PYTHONDONTWRITEBYTECODE=1`；夹具根 `TMPDIR=/tmp/nyaw05r.jdK3q/d-*`。

| 命令 | exit | 结果 |
|---|---|---|
| `$PY tests/test_life_v1_integration.py`（`-m unittest tests.test_life_v1_integration`） | **0** | `Ran 26 tests in 186.683s` / `OK` |
| `$PY -m unittest tests.test_one_day_life` | **0** | `Ran 17 tests in 75.864s` / `OK`（**断言未改**） |
| `$PY -m unittest tests.test_one_day_life_w05r` | **0** | `Ran 86 tests in 547.086s` / `OK`（**断言未改**） |
| 导入/语法自检（`life-v1-d/import_check.py`） | **0** | `IMPORT_OK` |
| 世界投影探针（`life-v1-d/room_probe.py` / `room_probe2.py`） | **0** | 该世界所有地点均无 navigation 声明 → 无家具 |

未跑（按比例验证要求）：A/B/C 各自定向套件（复用其已交付证据）、`check_life_v02.py`、`check_life_memory_v02.py`、
全仓/World/Supply/Memory/CLI 大套、`w05rr1c_neg.sh`（禁止执行）。未改真实 send/guard 路径（`life_pause`/`RequestSendGuard`/`Budgeted*` 一字未动），故未重跑发送专项。

---

## 6. 已接 / 未接 / 未验 / 真实阻塞

**已接（本地真实 owner + 真实 World + loopback 替身，可复现）**
1. A 接线：处境/预算分列、Body 列不因预算变化、容量四键进 AG-1、`BLOCK_ON_UNKNOWN` 未配。
2. 显式作品绑定：`latest_resumable` 不再是作品选择路径（cycle/status/report 三处）。
3. B 阅读链：真读 owner head+内容 → 取得范围 → 书签 → 未懂；收藏不读；旧书签 `needs_reread`。
4. 统一入口 12 动词 + `status` 技术状态 + `timeline`（只读、不补造）。
5. 有限共同时间：普通消息只观察；邀请决策；LR-3 规范暂停；LR-4 WAITING 保存点；AG-1/AG-2 有限共同活动；结束事件 → 新 eligibility；单前台保持。
6. 单写者 + 同数据根重启接续（预算 run identity / 阅读位置 / 作品绑定 / 暂停位）。

**未接 / 未验（如实）**
1. **Body 正向事实仍不存在**（A 的结论未被本包改变）：无 `get_body_signals` 顶层正向能力声明 → 真实身体只有 `LIMITED`/`UNKNOWN` 两态；本包未越写集。
2. **房间物件在夹具世界里为空**：探针证明生成的 coastal_town 包对**每个**地点都没有 navigation 声明，`observe_world` 因此 `local_environment=None`；入口如实回 `status=available, reason_code=WORLD_DECLARES_NO_LOCAL_ENVIRONMENT, objects=[]`，**不**编造物件。要有可观察的房间物件，需要一个声明了区域/家具的 World 包（owner 侧数据，不是本包接线问题）。
3. **C 的真实端到端未演示**：`_continuity_source` 只在配置 `NYA02_MEMORY_DB` 时建立；本包集成测试**没有** M0 数据库，所以场景里 C 未接线，cycle 如实报 `SITUATION_SOURCE_NOT_WIRED`/`PAST_SOURCE_NOT_WIRED`；`continuity_continue_object()` 的"历史改变继续对象"只有机制与断言面，**没有**用真实准入 past 端到端跑过 → 记为未验。
4. **canonical subject vs 实例 subject 的真实差异**：LR-3/LR-4 只接受 `chiyo.global`（传 Supply workspace subject 会被 `SubjectIdentityError` 直接拒绝，实测过），所以协议显式区分 `subject_id`（邀请必须匹配的实例主体）与 `canonical_subject`（owner 侧主体）。
5. **共同活动的完成证据未 ingest**：共同活动带着真实的 `CRITERIA_EXTERNAL_RESULT_MATCH` 完成合同被创建，但"把外部结束结果作为 LR-5 证据 ingest"需要 LR-5 自己的证据信封（本包未实现，也不允许用本地写入代替）→ `end()` 返回 `joint_activity_completion.ingested=false / COMPLETION_EVIDENCE_NOT_INGESTED`；因此共同活动在 owner 侧仍是 OPEN。回私人保存点的**新 eligibility 已经真实产生**，但把 eligibility 消费成 RESUME 需要一次 Decision+Adoption（本模块不代做）。
6. **真并发明确延期**：超出当前单前台合同的真正并发未实现（会话记录与返回值都写明 `DEFERRED_BEYOND_CURRENT_CONTRACT`），未伪造。
7. 未验：真实模型行为、真实时钟短跑/24h、外发、部署/自启、多实例/跨会话；未在真实部署的 gateway socket/profile 上跑过。

---

## 7. 一行结论

D 把 A（处境/预算分列）、B（真实阅读/书签/收藏/房间观察）、C（有来源历史读提示）接进**同一个入口与同一实例**，
作品/阅读材料/私人文档各自显式绑定，安静/暂停/重启/单写者合同不变；有限共同时间走完了
"普通消息只观察 → 显式邀请 → LR-3 规范暂停 → LR-4 保存点 → Decision+Adoption 有限共同活动 → 结束事件 → 新 resume eligibility"
这条真实 owner 链，并明确把"真正并发"与"共同活动完成证据 ingest"列为未接/延期。
