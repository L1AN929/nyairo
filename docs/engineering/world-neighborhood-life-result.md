# 街区连续生活（L 包）回执：多地点连续 episode + 停留的合法提前结束

本批在 `work/v03-w01-navigation`（HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`）上继续施工，
**全部 UNCOMMITTED**：没有 stage / commit / push / reset / clean / checkout，没有整仓复制，没有删除
任何文件或夹具。产品写集只有 `components/life/scripts/autonomous_life_cycle.py` 一处；新增两个测试
模块与本回执/runbook/笔记。

## 1. owned delta

| 文件 | 状态 | 内容 |
|---|---|---|
| `components/life/scripts/autonomous_life_cycle.py` | M | 停留的**选择式提前结束**通路（`_close_stay_by_choice`）、"关店不许再造在场"的安全边界（`_place_availability` + `_classify_stay_step` 两处拒绝）、`_observe_position` 一并刷新 `last_world_catalog`、新常量 `STAY_PHASE_ENDED_EARLY` / `STAY_EXIT_ABANDON` / `STAY_EXIT_PAUSE` |
| `tests/test_world_neighborhood_life.py` | 新 | 同实例多地点连续 episode（1 例，组合复用 `EpisodeFixture` 实例，不继承它的 TestCase） |
| `tests/test_life_stay_early_exit.py` | 新 | 提前结束正负 2 例（合法 ABANDON / 不合法 ABANDON / 关店后强续 → 拒绝 → 合法退出） |
| `docs/engineering/world-neighborhood-life-result.md` | 新 | 本回执 |
| `docs/engineering/world-neighborhood-life-runbook.md` | 新 | 怎么跑、怎么读 |
| `.agents/notes/implemented/architecture/2026-10-19-life-stay-early-exit-and-neighborhood-chain.md` | 新 | 本人笔记（Problem/Decision/Alternatives/Consequences/Verification） |

没有改：W/F/Body/Memory/Supply owner、`chiyo_bundle/world_journey.py`、`world_body_client`（两份）、
`life_integration_ag2.py`、`action_reality_ledger.py`、AG-0/AG-1/AG-2 的权限与单前台语义、
`world_move_opportunity_source.py`（无需改：停留机会的 fail-closed 已经在那里）、旧
`tests/test_world_daily_life_continuous.py` / `test_world_daily_life_plan_change.py`、
看板/管理文档/别人的笔记。

## 2. 产品 API（现在时）

`autonomous_life_cycle.py`：

- `:156` `STAY_PHASE_ENDED_EARLY = "ENDED_EARLY"`、`:159` `STAY_EXIT_ABANDON` / `STAY_EXIT_PAUSE`。
- `:2041-2050` `evaluate_once`：前台是停留 Activity 且 Decision 为 `PAUSE`/`ABANDON` 时，交给
  `_close_stay_by_choice`（在 `has_foreground` 通用分派之前，因此不会被当成"普通放弃"）。
- `:2752` `_close_stay_by_choice(base, obs, *, activity, decision, adoption_status, rejection)`：
  - 重读 Activity owner：`ACTIVE` → `STEP_REFUSED`/`OWNER_DID_NOT_APPLY_THE_EXIT`；`REJECTED*` →
    `OUTCOME_ADOPTION_REJECTED`；两种都不写 exit 事实、不动前台；
  - 真实时长：`_stay_began_at()`（LR-5 第一个 checkpoint）→ 本轮 `observed_at`；
    `remaining_seconds` 如实；
  - **不调用** `_record_world_stay_progress()`，所以提前结束永不满足"待满"判据；
  - 报告 `base["stay"]`：`phase`（`ENDED_EARLY`/`PAUSED`）、`exit_decision`/`exit_kind`、
    `started_at`、`observed_at`、`elapsed_seconds`、`remaining_seconds`、`completed:false`、
    `completion_claimed:false`、`resumable_as_stay:false`、`owner_status`、`owner_ended_at`、
    `owner_terminal_reason_code`、`owner_last_transition_ref`、`foreground_released`；
    `base["step_path"] = "STAY_ENDED_BY_CHOICE"`；`outcome` 沿用既有 `ABANDONED`/`PAUSED`。
- `:2851` `_place_availability(place)`：只读 `self.last_world_catalog`（W 目录投影），返回 owner 的
  状态字符串或 `None`（读不到不升级也不降级）。
- `:3010-3012` `_observe_position()`：同一次观察把 provider 的 `last_catalog` 一并刷进
  `self.last_world_catalog`（一个缝、一个时刻，没有第二次目录读）。
- `:3369-3389` `_classify_stay_step()` 两处新拒绝（都在写任何 checkpoint 之前）：
  - `STAY_ALREADY_STARTED`：这个停留已经有 LR-5 的第一个 checkpoint，再 START 会把起算点挪走；
  - `WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT`：owner 此刻说这个地点 `CLOSED` → 不写 checkpoint、
    不写完成、不释放前台（"关门之后的继续"和"到点完成"从此是两件事）。

## 3. Episode 实证（同一实例、单一 `INJECTED_CLOCK`）

`tests/test_world_neighborhood_life.py`：一段 episode、一个 runtime、一份数据根、真实本地 owner。
注入时刻只有 `Clock.now`（`INJECTED_CLOCK`，起始 = 下一个工作日 07:00 JST），整段只随本文件的
`advance()` 前进（本轮 15 次），结束时 `2026-10-19T09:10:30+00:00`。**不是真时钟的一天**。

每段都是"真实单跳出发"，身份与正时长全部来自 owner：

| # | 边 | walk_seconds | segment_status | 说明 |
|---|---|---|---|---|
| 1 | home → street_front | 90 | confirmed | 这次答复**丢了**：`STEP_UNKNOWN` → `reconcile_unknown_step` 同 action/execution 收敛为 `RECONCILED_SUCCEEDED`，`list_actions(status="UNKNOWN")==[]` |
| 2 | street_front → street_corner | 120 | started | |
| 3 | street_corner → park | 180 | started | |
| 4 | park → street_corner | 180 | started | 公园停留自然完成之后 |
| 5 | street_corner → cafe | 120 | started | 07:23 时这一支还关着（那一轮 `NO_ACTION`），17:56 之后同一规则真的走过去 |
| 6 | cafe → street_corner | 120 | started | 咖啡店停留被"关门"这一事实提前结束之后 |
| 7 | street_corner → street_front | 120 | started | |
| 8 | street_front → reading_point | 150 | started | 到店后 `stay:reading_point:READ_HERE:600` 才出现（07:23 那轮是 `CLOSED`，没有这个机会） |
| 9 | reading_point → street_front | 150 | started | |
| 10 | street_front → home | 90 | started | |

每段都断言：`outcome=IN_TRANSIT`（出发≠到达）、destination 是 owner 公布的那个、owner 的 due 之前
位置**不变**、按 due 结算后位置才是目的地、`remaining_seconds>0` 且 ≤ 该边秒数。合计 1320 秒，全部
正时长。

两处**条件驱动**的改变（同一条固定替身规则，只读当轮候选 + owner 的营业事实；路线只在替身自己的
出门计划里，产品里一行没有）：

- **A. 街口 07:23**：`world_place_cafe_availability_CLOSED` 进了这次 `DecisionRequest`；
  `place:cafe` 仍在单跳候选里（闭店不拦出发），但这一轮选了 `NO_ACTION`（`llm_calls=1`、前台空、
  0 副作用、计划指针没前进）。同一计划推进到 17:56 之后，同一条规则读到 `OPEN` → 真的出发并到达。
- **B. 咖啡店里 17:57 开始停留**（`stay:cafe:SIT_AT_CAFE:300`），18:00 店关门；那一轮模型读到
  `world_place_cafe_availability_CLOSED` 后主动 `ABANDON`。

停留的两种结局在**同一次 episode** 里都发生：

- **自然完成（公园）**：`START`（`stay:park:SIT:300`）→ `ONGOING`（150s，`progress_state` 不是
  `COMPLETION_ELIGIBLE`，前台仍是它）→ `END`（450s ≥ 300s，`COMPLETION_ELIGIBLE`，Activity
  `COMPLETED`，前台释放，LR-5 记下到点 checkpoint）。
- **提前结束（咖啡店）**：`outcome=ABANDONED`、`step_path=STAY_ENDED_BY_CHOICE`、
  `stay.phase=ENDED_EARLY`、`planned_seconds=300`、`elapsed_seconds=180`、
  `remaining_seconds=120`、`completed:false`、`completion_claimed:false`、
  `owner_status=ABANDONED`、`owner_terminal_reason_code=ABANDONED_BY_DECISION`、
  `owner_ended_at` 有值、`resumable_as_stay:false`、`foreground_released:true`；Activity 上**没有**
  `checkpoint:stay:` 到点 checkpoint；随后 `cafe → street_corner` 的 MOVE 合法并真的走到。

出门前/回家后（沿用既有机制，未重做）：真实 Supply 新版本 → 合法 safe-boundary 打断（真实共处
邀请）把私人 Activity 停在保存点（`WAITING` + `checkpoint_ref` + OPEN eligibility）→ 出门 → 回家
`RESUME`（`adoption_status=APPLIED`、`checkpoint_ref` 仍是打断前那一个、
`continuity_consumed_eligibility_ref` 是那条 eligibility）→ `CONTINUE` 落成新的 Supply 版本。

M0：公园那次停留（`complete=True`）经 AG-2 既有 `emit_experience_handoff(world_refs=…)` 进真实
M0，行文是中性 World 模板（`在真实世界里发生了一件已确证的事；世界事实
world_fact:stay:park:SIT:300、world_fact:place:park:r4；发生在 …`），`artifact_ref=null`，
不含"真实修改/新版本"；这一行**真的出现在**下一次 `DecisionRequest` 的 `prior_experiences` 里
（`prompt_view` 形状），同一次请求还带着 `world_place_*` 的营业事实。

无素材轮仍是 `NO_MATERIAL` + `llm_calls=0` + 0 次替身调用。

## 4. 提前结束的正负（`tests/test_life_stay_early_exit.py`，2 例）

1. `test_a_stay_can_be_left_early_and_a_new_move_is_legal_again`（正）：公园停留 `START` →
   `ONGOING` → 合法 `ABANDON`；`elapsed=180 < 300`、`remaining=120`、
   `owner_terminal_reason_code=ABANDONED_BY_DECISION`、无 `checkpoint:stay:`、前台清空、Activity
   `ABANDONED`；随后 MOVE 合法。
2. `test_an_illegal_exit_is_refused_and_a_closed_place_is_never_claimed`（负 + 诚实出口）：
   - 不合法退出（`ABANDON` 投给只允许 `DEFER/START` 的候选）→
     `DECISION_FAILED` / `INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`，
     **没有** `stay` 事实、Activity 仍 `ACTIVE`、前台还是它；
   - 店关门后坚持 `CONTINUE`（时间早已过声明 300s）→ `STEP_REFUSED` +
     `refused=WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT` + `progress_state=None`（不是
     `COMPLETION_ELIGIBLE`），前台**不释放**，Activity 仍 `ACTIVE`，没有到点 checkpoint；
   - 随后合法 `ABANDON`：`ENDED_EARLY`、`elapsed=390 ≥ 300` 但 `completed:false`、
     `remaining_seconds=0`、owner `ABANDONED`——"时间过去了"≠"待满了"。

## 5. 命令与真实退出码

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=12345 TMPDIR=/tmp/nyaw05r.jdK3q/nb-life-r1
bash $S/neighborhood-life/nb_run.sh <module>   # 每模块独立子进程，打印 PY_EXIT / MODULE_EXIT
```

`nb_runner.py` 在导入任何测试模块之前把 `shutil.rmtree` 换成 no-op（夹具保留），断言一字未改；
被测文件里没有 `TemporaryDirectory`/`unlink`/`rmtree`/socket unlink。`TMPDIR` 指向
`/tmp/nyaw05r.jdK3q/nb-life-r1`，于是所有 home 都落在 `/tmp/nyaw05r.jdK3q/nb-life-r1/w05inst-*`、
`…/w05world-*` 并被保留。

| 模块 | 结果 | 真实退出码 | 日志 |
|---|---|---|---|
| `tests.test_world_neighborhood_life` | `Ran 1 test … OK`（48.33s） | `MODULE_EXIT 0` | `/tmp/nb-final.log`（首跑 68.06s 在 `/tmp/nb-all.log`） |
| `tests.test_life_stay_early_exit` | `Ran 2 tests … OK`（32.67s） | `MODULE_EXIT 0` | `/tmp/nb-final.log`（首跑 39.26s 在 `/tmp/nb-early.log`） |
| `tests.test_world_daily_life_continuous`（受影响旧 exact ID） | `Ran 1 test … OK`（21.16s） | `MODULE_EXIT 0` | `/tmp/nb-final.log`（首跑 37.15s 在 `/tmp/nb-all.log`） |
| `tests.test_world_daily_life_plan_change`（受影响旧 exact ID） | `Ran 1 test … OK`（16.88s） | `MODULE_EXIT 0` | `/tmp/nb-final.log`（首跑 25.73s 在 `/tmp/nb-all.log`） |

门禁（repo cwd，node v22.19.0）：

```
ok: 25 note(s) tree and relative links verified      TREE_EXIT=0
ok: 25 note(s) verified                              FORMAT_EXIT=0
```

`git diff --cached` 空（0 staged）、`HEAD` 仍 `7f09d26df64e054faf51ba35ad85c64f73a5a837`、
`git diff --check` 无空白错误。笔记从 23 条变 25 条：本批新增 1 条，另 1 条来自并行的 owner 验证包
（`world-neighborhood-owner-chain-result.md`，非本写入者）。

## 6. 没做 / 真缺口

1. **只有 `ABANDON` / 明确结束这一条提前退出。** `PAUSE` 会被准确报告（`phase=PAUSED`、
   `resumable_as_stay:false`、前台按 owner 的记录释放），但**没有**完整的"暂停期间不计时长"的恢复
   计时系统：暂停区间没有 owner 侧记录，所以本批不假装停留可以被恢复。
2. **"等开门"仍然没有 owner due-time。** 街口那一轮她只是 `NO_ACTION`（合法的"什么都不做"），
   不是排队等待；本批只证明"读到 CLOSED 就不去、读到 OPEN 才去"。
3. **M0 的 ABANDON handoff 文案仍是通用终止模板**（"放下了这件事（作品仍然留着）"）。AG-2 在
   ABANDON 上发的 handoff 没有 world refs，consumer 因此走通用句；`life_experience_memory_consumer.py`
   不在本批写集，未改，如实记录。
4. **`prompt_view` 不带 `action_ref`/`owner_result`**（World 行的 `owner_result` 是 `null`）：所以
   "历史真的进了下一次输入"是按行文里的 `world_fact:stay:…` 与行文本认的，不是按结构化字段。
5. **预算/计费**：本次仍是"暂停不重置计数、替身调用计数"层面的证据，没有重做 R1c 的非零计费端到端。
6. **不区分 area 级停留**：世界仍只按 `place_id` 判定"她在哪儿"。
7. **没有真实模型、外网、24h 长跑、部署/自启、provider 密钥配置**；全部授权都未使用。
8. **夹具目录**：`/tmp/nyaw05r.jdK3q/`（`nb-life-r1/` 下本批新增，`i-r1-*`/`ir2-*`/`ir3-*`/
   `w05inst-*` 等旧目录）一律保留，未删未清。
