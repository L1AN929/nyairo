# I-r1 回执：连续一天（保存点 → 出门 → 停留 → 回home → 消费 → 恢复私事 → 经历接续）

- 工作区：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`），branch
  `work/v03-w01-navigation`，HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）。
- 状态：**UNCOMMITTED**。未 stage / commit / push / reset / clean / checkout / 整仓 copy；
  未删除任何文件或夹具（`/tmp/nyaw05r.jdK3q/ir1-*`、`w05inst-*` 保留）。
- 本回执是 I 包原失败/未完成项（`docs/engineering/world-daily-i-result.md` §8 第 1–5 条）的
  **接续**；旧回执原样保留，不反写。
- 时钟：演练用**两个显式声明的时刻**（`INJECTED_CLOCK`）：Life 时钟贴着墙钟
  （`real_now+60s`，只由本文件 `advance()` 推进），营业时刻钉在一个真实的周六中午（W 目录只按它
  读，所以"店工作日才开"是确定事实）。二者都不随结果变化，都不是墙钟冒充。

## 1. owned delta

| 文件 | 改动 |
|---|---|
| `components/life/scripts/world_move_opportunity_source.py` | `STAY_VERB`/`STAY_REF_PREFIX`/`STAY_SECONDS_BY_AFFORDANCE`/`STAY_DESCRIPTOR_PREFERENCE`/`STAY_DESCRIPTOR_QUIET`；`stay_target()`/`parse_stay_target()`/`_stay_descriptor()`/`observed_stay_opportunities()`；`opportunity_view()` 增 `STAY` 种类与 `affordance`/`stay_seconds`；provider 先读目录再产出停留、新增 `observed_now`（观察时刻）与 `_observation_moment_iso()` |
| `components/life/scripts/autonomous_life_cycle.py` | `STAY_REF_PREFIX`/`STAY_ACTIVITY_KIND`/`WORLD_STAY_FACT_PREFIX`/`WORLD_STAY_FACT_KEY`/`STAY_PHASE_*`；模块级 `stay_plan()`/`_epoch_seconds()`/`_seconds_between()`；`_chosen_target_ref`/`_chosen_place`/`_candidate_is_world` 认 `stay:`；`_stay_adoption_spec()`；`_stay_step_spec()`；`_foreground_stay_activity()`；`_stay_plan_from_activity()`；`_classify_stay_step()`；`_stay_began_at()`；`_record_world_stay_progress()`；`evaluate_once` 停留前台只给 stay checkpoint；`_refresh_world_opportunities` 过期上一轮机会（AG-0 `expire_opportunity`） |
| `tests/test_world_daily_life_play.py`（新） | 4 个机会层用例（公园中午 SIT/300；周六 06:00 店 CLOSED + 无 affordance + 无停留 + 门口可走；公园 23:00 无停留；目录不可读恒空） |
| `tests/test_world_daily_life_continuous.py`（新） | 1 个同实例连续演练（见 §3、§4） |
| `docs/engineering/world-daily-i-r1-result.md`、`world-daily-i-runbook.md`（补连续用法）、`life-v1-capability-status.md`（现状） | 本回执与运行说明 |
| `.agents/notes/implemented/architecture/2026-10-17-world-daily-continuous-stay-and-plan-change.md`（新） | 本批决策笔记 |

未改：W/F 的 World/Body/Memory/Supply owner 文件、AR-0 的权限/状态真相、AG-0/AG-1/AG-2 授权与
单前台语义、`action_reality_ledger.py`、`life_integration_ag2.py`、`activity_progress_completion_lr5.py`、
`activity_waiting_agenda.py`、别人的笔记与 AGENTS、管理 00/03/方案文档。

## 2. 当前 API（调用的都是既有 owner 入口）

- 机会：`observed_stay_opportunities(observation, *, catalog, occurred_at, observed_at, valid_for_seconds)`
  → 至多一条 `{"target_refs": ["stay:<place>:<descriptor>:<seconds>"], ...}`，`action_grant` 由
  `opportunity_view` 恒为 `false`。
- 计划解析：`stay_plan("stay:street_front:LOOK_AROUND:180")` →
  `{"place","descriptor","seconds","target_ref"}`。
- Life step：`_stay_step_spec(target, phase="START"|"END")` →
  `{step_kind: "INTERNAL_CHECKPOINT_STEP", action_kind: None, payload_spec: {stay_phase, stay_place,
  stay_affordance, stay_planned_seconds, stay_target_ref, stay_at}, idempotency_key}`。
- 进度：`_record_world_stay_progress(...)` → LR-5 `ingest_progress_evidence`，观察事实里带
  `world_location_ref`/`world_place_ref`/`stay_started_at`/`stay_elapsed_seconds`/`stay_planned_seconds`，
  只有 `complete=True` 才写 `world_stay_ref`（= 完成判据的期望值）。
- 读回：`runtime.progress_read.list_checkpoint_updates(activity)`（起始时刻）、
  `runtime.activity_read.get_current_life_view()/get_activity()`（前台与 kind）、
  `cycle.settle_world_journey(observed_at=...)`（世界自己的到达）。

## 3. 连续演练（同一实例、同一数据根、一个 runtime）

用例：`tests.test_world_daily_life_continuous.DailyContinuousDrillTest.test_one_instance_lives_the_whole_day_through_real_owners`
（真实 Life Supply socket 服务、真实 `WorldBodyService`+真 `gateway.sock`、真实
`examples/worlds/daily_neighborhood_v1`、真实 Body 后果、真实 M0/Memory 准入、真实 Activity/LR-5/LR-4；
模型是确定性替身，无外网）。夹具 `/tmp/nyaw05r.jdK3q/w05inst-*` 保留。

| 步 | 谁做的 | 真实后果（owner 侧可回读） |
|---|---|---|
| 0 静默 | `evaluate_once`（无 provider、无 artifact） | `NO_MATERIAL`、`llm_calls=0`、0 调用 |
| 1 出门前私事 | AR-0 → 真 Supply → AG-2 handoff → M0 | 作品 `version 6a85b109…→79809dce…`、`m0_rows=1`、行 `delivery_status=VERIFIED` |
| 2 合法 safe-boundary 打断 | `FiniteCoPresence.begin/end`（真实邀请事件） | 私人 Activity `WAITING`、`foreground_released=true`、`wait_dep:co_presence-inv:ir1:1`；共处 `COMPLETED`；`relig:99ee3bd…` OPEN；`llm_calls=0` |
| 3 出门 + 不消费分支 | 一次观察同时给出 `place:`/`object:`/`stay:`；Decision 选路 | `IN_TRANSIT`；那一份仍在 placement、`consumptions=={}`、无该物件的 Body 后果 |
| 4 闭店改主意 | 门前街重读 W 目录 | `competing=['place:home','place:reading_point','place:street_corner']`；`cafe=CLOSED`、`affordances=[]`、`observed=not_observed`、**单跳候选里没有 `place:cafe`**；`park=OPEN`；随后 Decision 选在原地停留（不往闭店那一支走） |
| 5 停留起止 | AG-1 START → AG-2 `STAY_AT_OBSERVED_PLACE` → 内部 checkpoint（无 Action） | START：`checkpoint` 由 LR-5 记在 `world_fact:place:street_front:r2`；提前一轮 `elapsed=120<180` → `ONGOING`、`progress_state≠COMPLETION_ELIGIBLE`、前台仍是它；到点一轮 `elapsed=240≥180` → `progress_state=COMPLETION_ELIGIBLE`、`checkpoint=world_fact:stay:street_front:LOOK_AROUND:180`、Activity `COMPLETED`、前台 `null` |
| 6 回家 + 暂停 | 出发 → 置暂停位 → 暂停轮 → 结算 | 暂停轮 `PAUSED_BY_OPERATOR`、`llm_calls=0`、调用数不增、仍在街前；已出发段照常 `arrived=true` 到家；`utc_day_calls`/`run_window_calls`/`run_id` 前后一致 |
| 7 消费 | AG-1 CONSUME → AR-0 → gateway → Resolver | placement 消失；`consumptions` 恰好 1 条 `object_id=i-food-riceball-01`、`execution_id` 与回执一致；该 execution 的 Body 后果恰好 1 条且非营养；`progress_state=COMPLETION_ELIGIBLE`；前台 `null` |
| 8 回到私人保存点 | RESUME（owner 自己的资格）→ CONTINUE | `decision=RESUME`、`APPLIED`、`checkpoint_ref` 与打断前相同；`continuity_consumed_eligibility_ref=relig:99ee3bd…`；随后 CONTINUE `26da82fb…→1285ef3b…` |
| 9 经历接续 | `read_continuity()`/`_read_resume_point()` → 再一次真实 CONTINUE | M0 里那一行 `owner_result=rset:457c43…` → `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`、`selected_by=CONTINUITY_HISTORY`、`expected_head_source=OWNER_CURRENT_HEAD`、pin == owner 当前 head；该步 `ACTED` 且 `selected_by=CONTINUITY_HISTORY`、新版本落地；弱线索 → `selected_by=BOUND_WORK`、对象不变 |

## 4. 命令与真实 exit

venv `/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`，
一律 `PYTHONDONTWRITEBYTECODE=1`、`TMPDIR=/tmp/nyaw05r.jdK3q`、repo cwd，scratch runner
`i_daily_runner.py`（只把 `shutil.rmtree` 换成 no-op，断言一字未改）。

| 命令 | exit | 结果 |
|---|---|---|
| `i_daily_runner.py tests.test_world_daily_life_play` | 0 | `Ran 4 … OK` |
| `i_daily_runner.py tests.test_world_daily_life_continuous` | 0 | `Ran 1 … OK`（22–35s）；**连跑两次**均 exit 0（`ir1-stability-1/2.log`） |
| `i_daily_runner.py tests.test_world_daily_life_consume` | 0 | `Ran 6 … OK`（只复测与本 provider 相关的这 6 条，不重刷旧模块套件） |
| 三模块合并跑（play+consume+continuous） | 0 | `Ran 11 … OK`（72.7s），`PY_EXIT 0`（`ir1-final-2.log`） |
| `node --experimental-strip-types …/verify-agent-note-tree.ts` | 0 | `ok: 20 note(s) tree and relative links verified` |
| `node --experimental-strip-types …/verify-agent-note-format.ts` | 0 | `ok: 20 note(s) verified` |
| `git diff --check` | 0 | 无空白错误（本批 8 个文件，`git diff --cached` 为空，HEAD 未动） |

日志：scratch `world-daily-i-domestic/ir1-final-*.log`、`ir1-continuous-*.log`、`ir1-consume.log`。

## 5. 负例、权限与恢复（本批）

- **停留不产生动作**：`step_kind=INTERNAL_CHECKPOINT_STEP`、`action_kind=None` → AG-2 不铸 Action，
  因此没有 AR-0 proposal/授权/回执/后果（演练里停留轮 `world_verb` 字段不出现）。
- **时间不能伪造完成**：提前一轮 `elapsed=120` 时证据不带 `world_stay_ref`，`progress_state` 不是
  `COMPLETION_ELIGIBLE`，Activity 仍 ACTIVE。
- **位置不符不记账**：`_classify_stay_step` 每次都用真 gateway 读位置；不符时
  `WORLD_POSITION_DOES_NOT_MATCH_STAY`、不写证据（代码路径有，本批演练命中过一次过期机会的原位
  拒绝：候选 `stay:street_corner:…` 而人在 `park`）。
- **目录不可读 ≠ 可以待着**：`status!=available`/地点非 `OPEN`/地点不在目录 → 0 条停留机会。
- **权限**：没有新增授权、没有旁路；消费仍走一次性授权链；停留根本没有授权可拿。
- **恢复**：私人 Activity 的 checkpoint 与 eligibility 都来自 owner；RESUME 用的是 owner 自己的资格。

## 6. 本批发现的**既有**问题（未顺手改，W/F owner 范围）

1. **World 计时出行偶发 `UNKNOWN`**（owner 侧，未修）：当若干次出发落在同一个墙钟秒内、而结算
   时钟在它们前面时，World 会把一次真实出发答成 `UNKNOWN`——journey 落成 `blocked`
   （`error.reason_code=UNKNOWN`、段仍 `pending`），而 travel 记录是 `IN_PROGRESS`。
   证据（scratch `probe_legs.py` 直连正式提交口、不经 Life）：`/tmp/nyaw05r.jdK3q/w05inst-*` 的
   `world_journey.json`/`world_travel_delivery.json`；AR-0 侧同一执行 `status=UNKNOWN`、
   `provider_operation_ref=world-exec:…`、无回执。演练的处理是**先对账**
   （`settle_world_journey` 读 owner 自己的旅程/旅行记录并结算），只在真的没走成时才重新决定，
   绝不在未对账时盲重发。**因此本批的连续路线收敛到 owner 侧稳定的一段（家→门前街→家）**，
   多段单跳（街口/咖啡店门口/公园）见 §6.2。
2. **多段单跳在本环境下不稳定**：`家→门前街→街口→咖啡店门口→公园→…` 这类长链在
   `probe_legs.py` 与演练里都能走到第 3–6 段后命中上一条的 `UNKNOWN`（不同次跑命中位置不同，
   不是固定脚本问题）。按要求不修 W/F；这条留给 owner 侧定点修复，本批不声称"多段长链全绿"。
   闭店/店门口/园内停留的事实证据因此分别在：本回执 §3 步 4（owner 当下事实 + 单跳边界）、
   `tests/test_world_daily_life_play.py`（门口可走、店内无机会）、W 自己的
   `components/worldgen/tests/test_edited_world_runtime.py::test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty`。
3. **AG-1 候选上限（16）会被上一刻的机会占满**：本批在 cycle 侧用 AG-0 自己的
   `expire_opportunity` 把本轮不再支持的机会收走（见笔记），修复后演练候选数稳定 7–9 条。
4. 旧回执 §7 的三条既有红灯（W 计时出行墙钟依赖、W05R 跨日预算墙钟依赖、D-final 重启负控哈希
   种子依赖）**原样保留**，本批未跑、未改、也未用它们挡产品。

## 7. 真未完成（不夸称）

1. **一次同实例的"多段单跳长链"**（街口/咖啡店门口/公园/再回家）在 owner 侧的 `UNKNOWN` 修好
   之前跑不稳；本批交付的是同一实例上"保存点 → 出门（真出发/真到达）→ 闭店事实下改主意 →
   停留起止 → 回home（含暂停）→ 消费 → 恢复私事 → 经历接续"这一条，**不是**含 6–8 段的整链。
2. **停留描述符/时长是固定表**，不是配置；`QUIET_STOP` 兜底没有世界包声明依据（只是一个安静的
   暂停，明确不是世界 affordance）。
3. **室内 area 级停留**（"在卧室"而不是"在家"）今天不区分，只按 `place_id` 判。
4. **真实产品模型、24h/真实时钟长跑、部署、多实例、真并发**：均未做（授权外）。
5. **W/F owner 侧的两个问题**（§6.1、§6.2）需要 owner 定点修；本批只报告，没有造第二个 manager/store。

## 8. 工作区状态（本批相关）

```
 M components/life/scripts/autonomous_life_cycle.py
 M components/life/scripts/world_move_opportunity_source.py
?? tests/test_world_daily_life_play.py
?? tests/test_world_daily_life_continuous.py
?? docs/engineering/world-daily-i-r1-result.md
 M docs/engineering/world-daily-i-runbook.md
 M docs/engineering/life-v1-capability-status.md
?? .agents/notes/implemented/architecture/2026-10-17-world-daily-continuous-stay-and-plan-change.md
```

同仓其它改动（W/F/A/B/C/D 与另一位 writer 的 3 条旧测试）不是本批的，未触碰、未回滚。
