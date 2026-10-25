# G1 中断/接续：可执行场景定位（2026-10-22）

状态：本阶段只读定位，已交付。
范围：0 产品调用、不起服务、不写实例、不改业务代码。下文所有命令都实际执行过，退出码为真实值。
基线：实例 `/root/nyairo-day-20261022`，Life 嵌套实例 `/root/nyairo-day-20261022/one-day`，
run `run:one-day`，持久账本 12 次，配置上限 96/96，lease FREE，无活进程。

## 场景语义

两个"中断"不是同义词，本场景必须用前者。

**1. 运营中断 = Life 侧 pause 位。** 文件是 `<life state root>/life_pause.json`
（实测 `/root/nyairo-day-20261022/one-day/life/state/life_pause.json`）。
写入口 `autonomous_life_resident.py:309-318 set_paused()` → `life_pause.set_paused`，
CLI 是 `components/life/scripts/one_day_life.py:1807-1810` 的 `pause`/`resume`
（`:844-848 set_pause`）。它是纯文件写，实例停止时也有效（`:310-311` 注释）。
它在**每次请求的发送边界**生效：`autonomous_life_resident.py:812` 构造
`assembly.pause_request_admission(self.state_root)`，`:813-815` 把同一对象作为
`admission`/`pause_guard` 交给 model cognition。暂停窗口的结果是
`PAUSED_BY_OPERATOR`（`:1021-1024`），`llm_calls=0`，且不推进 Activity。
它刻意不碰 World 的 `world_control.json`（`:312-315`）。

**2. 活动内等待 = Activity 状态机的 WAIT/RESUME。** WAIT 释放前台、保留同一 Activity /
同一材料 / 同一页（note `2026-11-15-v04-life-pacing-breakpoint-and-resume.md:34`）；
恢复只能由一轮**新鲜 Decision** 自己选 `RESUME`，不自动恢复、不重放旧 Decision
（同 note `:64-65`、`:83-85`），resume 选择走
`autonomous_life_resident.py:890 cycle.resume_selection`，完整链见
`tests/test_v04_life_pacing_chain.py:403`，跨进程见 `tests/test_life_v1_restart.py:341`。

**结论：两者不同义，不能互相替代。** pause 只按位拦截新决定，不把 Activity 置 WAIT、
不释放前台；WAIT 是活动内部依赖（作品 head / 材料下一版 / 身体恢复），WAIT 期间仍可考虑
新活动。G1 的"外部中断"用 pause；"恢复不重放旧决定"由 AG-1 的候选撤回保证
（前台 ACTIVE 时不再给 START，`tests/test_life_v1_restart.py:404-413` 有具名断言）。
G1 的中断场景与重启场景共用同一组事实，但报告必须分开写：中断=暂停窗口 0 请求且不推进；
重启=新进程同 run、同账本、lease 重新获取后不重放。

## 现场事实

**活进程与 lease（2026-10-22 23:41 CST / 15:41 UTC 实测）**

- `ps -eo pid,etime,args | grep -E "one_day_life|autonomous_life|phone.py serve|live_day|owner-supply|owner-world"`
  → 无匹配行。没有 resident、没有 phone serve、没有 Supply/World owner 进程。
- `day_capability_probe.py status --root /root/nyairo-day-20261022` → shell `EXIT=0`；
  报告 `status=READY`、`exit_code=0`、`read_only=true`、`writes=false`、`model_called=false`；
  `lease.status=FREE`（`run/life_activity_authority.lock`，read_only_flock）。
- `phone.py life-preflight`（source `/root/.nyairo-model.env` 后）→ 报告 `status=READY`、
  `exit_code=0`、`run_ready=true`、`blocked=[]`；`lease.status=FREE`；
  `model.configured=true`（provider `custom`，model `cn:deepseek-v4.1-flash`，只记录是否出现，不打印值）。
  未 source 时同一命令 `exit_code=1 / BLOCKED`，`blocked=["model"]` —— 模型环境是进程级注入，
  不是实例文件；这是运行前必须做的一步。

**账本与预算**

- 持久账本文件（mode=ro 读计数，不读 tokens 正文）：`days={"2026-10-22": 12}`，
  `windows={"run:one-day": {"calls": 12}}`，`run_id=run:one-day`，tokens 条数 12。
- `phone.py life-preflight` 的 `switch.budget_today`：`cognition_calls.max=96`、
  `actions max 24 / used 4`、`rounds max 48 / used 9` —— 96/96 配置现场确认。
- probe status 里的 `utc_day_max=17`、`run_window_max=17` 是**上一段临时 segment cap 摘要**，
  不是配置总额度；与 preflight 的 96 对照即可区分。这一点此前父级已纠正，本次复核一致。
- 视图差异：`one_day_life.py status` 的 `resident.baseline.budget` 显示
  `history: {"2026-10-22": 11}`、`cognition_used_today=11`，与持久账本 12 不一致。
  以持久账本 12 为准；差异未进一步归因，只记录。

**当前 Activity 与可续项**

- probe status：`doing.activity_ref=null`、`doing.outcome=DECISION_FAILED`、
  `doing.status_word=BLOCKED`、`pause.state=absent/paused=false`。
- `one_day_life.py status --instance .../one-day`：`state.state_class=IDLE`，
  basis `no pause, no open Activity, no blocked window in the last record`；
  `work/reading/private_items` 全部报 `LIFE_SUPPLY_UNREACHABLE: [Errno 111] Connection refused`
  （此时 owner 未启动，属正常，不是数据缺失）。
- `stop_point`：`status=READY` 但 `resumable=null`、`selected_by=NOT_RESUMABLE`、
  `unknown_reason=BASELINE_ABSENT`、`artifact_id=null`、`foreground_activity_ref=null`。
  **当前没有前台活动，也没有可续作品。**
- Supply sqlite（`supply/data/life_supply.sqlite`，mode=ro，只取 id/lifecycle/title）：
  - `artifact:f1baf011adc44f9a90d0a028dccc3f52`「她自己的日常记录」→ **ARCHIVED**
  - `artifact:cf1428ff31ba4a59a2d20584e54dcbe9`「陶渊明《桃花源记》开篇」→ **ACTIVE**
  - `artifact:00ee668a60014d8491fc0d1b62260ac1`「她的私人日常事项」→ ACTIVE
  - `artifact:7d39dcca9d824c6583267b5141892199`「验收用测试阅读材料（合成）」→ ACTIVE
- 因此：**唯一可续的真实作品不存在**（work 绑定 f1baf 已 ARCHIVED，禁止复活、不得借旧指针）；
  唯一可续的真实活动是**阅读 ACTIVE 材料 cf1428（桃花源记）**。材料绑定仍是桃花源记，
  `CHIYO_LIFE_READING_ARTIFACT_ID` 指向该 id；私人事项里存在 `reading_position` 条目
  （`one_day_life.py:1089 reading_view` 读取；本次因 owner 未启无法读条目内容，只知类型字段）。
- `UNKNOWN` 不等于"没有活动"：World `status=UNKNOWN / WORLD_GATEWAY_MISSING`，
  body `UNKNOWN / NO_BURDEN_EVIDENCE`，这些是读不到，不是"她没在做"。

**服务现场**

- `ss -xl | grep nyairo-day-20261022` → `NO_LISTENER`。supply socket 文件存在
  （`run/supply-serve-1792678259-10351.sock`），但没有监听者；preflight 的
  `supply.probe=not_performed_read_only`、`stale=false` 只是文件与 marker 匹配，不是连通性。
- World gateway `one-day/world-home/run/gateway.sock` 不存在。
- 这不阻塞后续段：`live_day.run_segment(start_services="auto")`
  （`components/phone/live_day.py:978`、`:1124`、`_ensure_services:872-902`）会在段内通过
  `demo.serve_instance` 起 Supply/World 子进程，段结束在 `:1188-1189` 停掉自己起的子进程。

## Memory加载

**机制路径（具名）**

- `AutonomousLifeResident.start()` 在 `autonomous_life_resident.py:794` 调
  `_memory_ports()`；`:624` 读 `NYA02_MEMORY_DB` / `CHIYO_LIFE_MEMORY_DB`；
  `:635-636` 构造 `LifeExperiencePort(database, controls_db=NYA02_MEMORY_CONTROLS_DB, subject=owner)`；
  `:640-641` 返回 `MemoryAdmissionExperienceConsumer(enabled=True)`、`port.prompt_view`、port。
- `:820` 把 consumer 作为 `experience_consumer=` 传给 `build_production_life_runtime`。
- `:830-831` 仅当 Memory DB 环境名非空才提供
  `continuity_source = lambda: self._continuity_source(self.runtime)`。
- `_continuity_source`（`:643-684`）：owner 取 `NYA02_MEMORY_SUBJECT`（本实例 `chiyo`），
  controls 取 `NYA02_MEMORY_CONTROLS_DB`，`:667-668` 构造
  `MemoryControlLedger(controls, memory, owner=owner, conversation="life:chiyo")`，
  `:678` 构造 `LifeExperiencePort(memory, controls_db=controls, subject=owner)` 作为 `past_source`。
  因此 evidence + controls + owner 三者都来自**本实例绑定**，不是进程默认值。

**preflight 不足以证明加载。** `phone.py life-preflight` 只做 `mode=ro` 打开、quick_check、
sqlite_master（preflight 的 `verified_claims.not_performed` 明确列出
`memory owner construction` 未做）。它 READY 只能证明文件存在、可读、governance 健康。

**真实 resident 的可核实事实（运行期，全部来自 `read_status()`）**

- `continuity_wired`（`:1315`）——装配期事实，说明 continuity factory 已交给 runtime。
- `last_continuity`（`:1320-1323`）含 `past_state`、`source_states`、`read_error`、`llm_calls`
  ——**第一轮真实 cycle 之后才写**，这是"真的读了 M0"的唯一运行期证据。
- `last_error`（`:1340`）不含 `MEMORY_IMPORT_FAILED` / `MEMORY_PORT_FAILED` /
  `CONTINUITY_IMPORT_FAILED` / `CONTINUITY_SOURCE_FAILED`。
- `situation_wiring`（`:1314`）同轮给出。

**标级与 0/有限调用**

- 仅 `start()`（构造 runtime 与 factory）：标 **CONSTRUCTED/WIRED**，不算真正读。
- paused 段：能证明 `start()` 成功且 0 请求，但 paused 窗口不评估 cycle，
  `last_continuity` 不更新，仍不算读。标 **STARTED_ZERO_CALL**。
- 一次真实决策窗口（≥1 请求）后 `last_continuity.past_state` 出现（本库预期是"空但已接上"，
  因为 `life_outcomes=0`、`controls=0`）：标 **READ**。`PAST_SOURCE_NOT_WIRED` 才是没接上；
  空库只能说明这个新绑定里还没有准入经历，**不等于她没有过去**。
- 方案 A 的第 1 段会顺带产生这条证据，不需要额外调用。

## 方案与推荐

**方案 A（推荐）：既有产品级 pause/resume + 有界 run，场景选"阅读桃花源记"。**

- 0 新业务代码。工具全是现成的：
  `scripts/day_capability_probe.py run`（起 owner 子进程、应用段请求上限、
  报告落盘 `:394-434`）＋ `components/life/scripts/one_day_life.py pause/resume`。
- 为什么是阅读而不是工作：当前唯一 ACTIVE 的可续对象是阅读材料 cf1428；
  work 绑定 f1baf 已 ARCHIVED，不能复活，也不能拿它的旧指针当活动。
- 为什么不用 Activity WAIT 当"中断"：WAIT 是活动内部依赖，不拦请求、不跨进程；
  G1 要验的"中断时不推进、不发请求"只有 pause 位能给出。
- 已知限制：真模型是否在恢复后选 `CONTINUE` 不可控；若它选别的，本场景不通过，
  如实记录，不拿测试替身补成通过。

**方案 B（后备，离线机制证据，不是 G1 通过）：一次性真 runtime 场景测试。**

- 新增 1 个测试文件（例如 `tests/test_v04_g1_interruption_chain.py`），复用
  `DScenarioBase` / `EpisodeFixture` 的真 Supply owner、真 runtime，决策仍是脚本替身。
- 它与既有 `tests/test_life_v1_restart.py:341`、`tests/test_v04_work_revisit_runtime.py:454`
  同构（进程 A 写版本 → pause → 进程 B paused 0 请求 → resume → CONTINUE 同一 Activity）。
  **新意只有两点**：活动对象是阅读 ACTIVE 材料；显式断言 `last_continuity.past_state`
  非 `PAST_SOURCE_NOT_WIRED`（把 Memory 实际加载纳入同一场景）。
- 标级 EL3（真 owner/runtime，脚本决策），**不能**称 EL4，不能当 G1 通过。
- 只有在 A 因真模型不产生可续活动而受阻时才值得写；否则是重复既有绿测试。

**推荐：先 A。** 一次跑通可同时取得：中断窗口 0 请求、恢复后 CONTINUE 同一 Activity、
同 run 同账本、Memory 运行期读取。真实请求预计 ≤4，在 6 以内。B 不预先写。

## 精确执行与验收

**前置（0 调用，只读；父级可独立复跑）**

```bash
cd /mnt/d/nyairo-rc1-body-backend
source /root/.nyairo-model.env          # 只注入，不打印
python -B scripts/phone.py life-preflight --instance /root/nyairo-day-20261022
python -B scripts/day_capability_probe.py status --root /root/nyairo-day-20261022
```

通过条件：`run_ready=true`、`blocked=[]`、`memory.status=READY`、`lease.status=FREE`、
账本 `run:one-day` 仍为 12、`pause.paused=false`、无活进程。

**第 1 段：真实开始一条活动（预算 ≤2）**

```bash
python -B scripts/day_capability_probe.py run --root /root/nyairo-day-20261022 \
  --duration-seconds 120 --poll-seconds 2 --max-new-requests 2
```

看报告：`status=READY`、`exit_code=0`、`requests.same_run_window=true`、
`requests.delta` 至少 1、`doing.activity_ref` 非空、`changed.owner_versions` 至少 1。
若 `doing.activity_ref` 仍为空，本场景停在这里报"没有可续活动"，不硬造。

**中断（0 调用）**

```bash
python -B components/life/scripts/one_day_life.py pause \
  --instance /root/nyairo-day-20261022/one-day --reason g1-interruption
```

通过条件：`paused=true`、lease FREE、无 resident 进程、账本不变。

**第 2 段：证明中断不推进、不发请求（预算显式 1，预期实际 0）**

```bash
python -B scripts/day_capability_probe.py run --root /root/nyairo-day-20261022 \
  --duration-seconds 60 --poll-seconds 2 --max-new-requests 1
```

通过条件：`model.calls_this_segment=0`、`requests.delta` 全 0、
`changed.owner_versions` 全 0、`pause_quiet_fault.pause.paused=true`、
`doing.status_word` 反映暂停（`PAUSED_BY_OPERATOR` 出现在窗口记录里）。

**恢复（0 调用）**

```bash
python -B components/life/scripts/one_day_life.py resume \
  --instance /root/nyairo-day-20261022/one-day --reason g1-resume
```

**第 3 段：同 run 续同一活动（预算 ≤2）**

```bash
python -B scripts/day_capability_probe.py run --root /root/nyairo-day-20261022 \
  --duration-seconds 120 --poll-seconds 2 --max-new-requests 2
```

通过条件：`requests.same_run_window=true`、`ledger` 不重置、
`doing.activity_ref` 与第 1 段相同、`requests.delta` 至少 1、`changed.owner_versions` 至少 1；
决策是 `CONTINUE` 而不是新的 `START`（若报告没直接给 decision，看
`changed.wakes` 与 `week` 记录里的 decision/legal；不要用测试替身补）。

**Memory 运行期证据（第 1/3 段后各一次，只读）**

```bash
python -B components/life/scripts/one_day_life.py status \
  --instance /root/nyairo-day-20261022/one-day
```

看 `resident.continuity_wired`、`resident.last_continuity.past_state`、
`resident.last_error`。`past_state=PAST_SOURCE_NOT_WIRED` 才是没接上；空库的"空"不算没接。

**共享预算的落实**

- 段 1 `--max-new-requests 2`，段 2 `1`（paused 不消耗），段 3 `2`；总和 ≤4 ≤6。
- 每段结束读 `requests.after.run_window_calls`，必须单调递增且 ≤96；
  不传 `--new-run-window`，不传 `--run-id`。
- 段 cap 在 resident 内是 `min(配置, used_at_start + requested)`
  （`autonomous_life_resident.py:735-738`），不改持久配置；每段独立进程但共享同一账本与同一 run。

**后续工单写集（仅方案 A）**

- `docs/engineering/v04-g1-interruption-20261022-run.md`（1 份证据）
- 不写业务源码、不写测试、不改 notes/NOW/current/RUNBOOK。若第 1 段没有产生可续活动，
  停止并回到父级拍板，不追加施工。

**父级独立验收命令（≤5，均在上文）**

1. preflight：`run_ready=true / blocked=[] / memory READY / lease FREE`
2. 段 1 报告：`exit_code=0`、delta ≥1、`doing.activity_ref` 非空
3. pause 后 status：`pause.paused=true`
4. 段 2 报告：`calls_this_segment=0`、`requests.delta` 全 0、owner 版本 0
5. 段 3 报告：同 `activity_ref`、delta ≥1、`same_run_window=true`

## 未验证

- 本阶段没有跑任何真实段，没有产品模型调用，没有起 Supply/World 服务。
- 当前确实没有 open Activity；真模型能否自然开始并续同一阅读活动未验。
  "旧 START cand:a077fcadc6750011 承接受阻"只是历史事实，本次未重放、未复验。
- 阅读位置条目在 `private_items` 里，但本次 Supply owner 未启，无法读其内容；
  只能说材料 ACTIVE、绑定仍是 cf1428，不能说"她停在第几页"。
- World gateway 缺失，任何依赖 World 观测/外出/拿放的活动本轮不可验。
- Memory 运行期真读未验（`continuity=null`，`life_outcomes=0`）；preflight READY 不等于加载。
- 账本视图 11 vs 持久文件 12 的差异未归因。
- 方案 B 的测试未写、未跑；既有 `test_life_v1_restart` / `test_v04_work_revisit_runtime`
  本次未重跑，历史绿不当本次证据。
- 全量套件未跑，不称全绿。
