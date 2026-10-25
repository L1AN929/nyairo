# G1 中断/接续 · 既有入口真实场景执行（2026-10-22）

结论：**第 1 段受阻，没有进入中断/接续场景。** 本段唯一一次真实请求产生的是对第 4 段遗留活动
`actv_91acbd742551228b`（`GO_TO_OBSERVED_PLACE`）的主动暂停：`decision=PAUSE`、
`disposition=SELECTED_FOR_PAUSE`、`reason_codes=["SAFE_BOUNDARY_PAUSE_RECORDED"]`，
`actions=[]`、owner 版本增量 0、没有本段新开活动，材料是 World `REST`/pose 事实与合成件，
不是场景指定的桃花源记。按任务书停止条件，**未执行 operator pause、暂停负控段和恢复段**。
Memory 运行期读取在本段已被证明（新绑定、空库 `NO_ADMITTED_HISTORY`），单独标级。

本报告只证明"某一留活动被主动暂停"，不证明 G1 中断/接续，也不证明"下一次继续真实作品"。

## 执行基线（第〇步，只读）

时间 2026-10-22 23:48–23:49 CST（15:48–15:49 UTC）。全部命令实际执行，退出码为真实值。

| 项目 | 事实 |
| --- | --- |
| 实例 / Life 嵌套实例 | `/root/nyairo-day-20261022` / `/root/nyairo-day-20261022/one-day` |
| 活进程 | `ps` 匹配 `one_day_life\|autonomous_life\|phone.py serve\|live_day\|owner-supply\|owner-world` → 0 行 |
| lease | `FREE`（`read_only_flock`，lock 路径 `one-day/life/state/run/life_activity_authority.lock`） |
| 持久账本 | `run:one-day` = 12，`utc_day` = 12，`run_id=run:one-day` |
| 配置 | `CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY=96`、`MAX_TOTAL_REQUESTS_PER_RUN_WINDOW=96`、`PER_UTC_DAY=96` |
| preflight | shell 0，`READY`、`run_ready=true`、`blocked=[]`、`model.configured=true`（custom / cn:deepseek-v4.1-flash） |
| Memory | `READY`、owner `chiyo`、`recall_ready=true`、`BOUND_READY_READONLY_VERIFIED`；DB 在实例内 `one-day/life/memory/evidence.db` |
| operator pause | `life_pause.json` 不存在，`state=absent`、`paused=false`（原状态，本单未改） |
| 当前活动 | probe `doing.activity_ref=null`、`outcome=DECISION_FAILED`、`status_word=BLOCKED`；`state_class=IDLE` |
| 可续作品 | 无。`work_selection` 仍指向 `artifact:f1baf...`（ARCHIVED），`stop_point.resumable=null` |
| World | `UNKNOWN / WORLD_GATEWAY_MISSING`（服务未起，属预期；段内 `start_services=auto` 自起） |

基线副本：`20261022-g1-interruption-exec-baseline-probe-status.json`、
`-baseline-preflight.json`、`-baseline-life-status.json`、`-baseline-prev-run-report.json`（上一段 run 报告原样复制）。

## 第 1 段：真实请求（预算 2，实际 1）

命令（WSL，`PYTHONDONTWRITEBYTECODE=1`、`/tmp/rc104-venv/bin/python -B`，仅进程内 source `/root/.nyairo-model.env`，不打印密钥）：

```bash
scripts/day_capability_probe.py run --root /root/nyairo-day-20261022 \
  --duration-seconds 120 --poll-seconds 2 --max-new-requests 2
```

- 实际 shell `0`，stderr 空；段窗口 `15:49:32–15:51:39 UTC`（23:49:32–23:51:39 CST），ticks 334。
- 报告 `status=READY`、`ok=true`、`exit_code=0`。
- 账本 12 → 13，`delta={run_window_calls:1, utc_day_calls:1}`，`same_run_window=true`、`ledger_reset=false`、未申请新窗口。
- `model.calls_this_segment=1`，真模型 `mrun:real:2326bd80a61fd4a3`，`decision_route=COGNITIVE_REQUIRED`。
- 段内自起 Supply/World 子进程 2 个，段末 `services_stopped.stopped=true`；lease 事后 `FREE`。

### 本段真实业务事实（白名单，来自 journal 与 status）

| 字段 | 值 |
| --- | --- |
| Decision | `dec:6dfe73a7966b7c21`，`PAUSE`，`recorded_at 23:49:50+08:00` |
| 选中候选 | `cand:2bdfcf2718977fca`，`candidate_kind=CONTINUE_CURRENT`，`disposition=SELECTED_FOR_PAUSE`，`source_kind=CURRENT_ACTIVITY`，`reason_codes=["SAFE_BOUNDARY_PAUSE_RECORDED"]` |
| Activity | `actv_91acbd742551228b`，`activity_kind=GO_TO_OBSERVED_PLACE`，`title=去她观察到的地方` |
| Activity 状态 | `ACTIVE → PAUSED`（`transition_type=PAUSE`，`reason_code=VOLUNTARY_PAUSE`），`paused_at 23:49:34+08:00` |
| Activity 起始 | `started_at 2026-10-22T22:11:02+08:00`，**本段之前**，来自第 4 段 `dec:f1ca026164e400f0` / `cand:a077fcadc6750011` |
| Activity 绑定 | `checkpoint_ref=chk:stage:001`、`origin_ref=world_fact:place:home`；`source_refs` 含 `world_fact:stay:home:REST:300`、`bodyact:pose:*` 与合成件 `artifact:7d39...` |
| 动作 | `changed.actions=[]`，`last_action_ref=null`（自创建起一直没有动作） |
| owner 版本 | 本段前后均为 3（`4430a97f…`、`5da41e57…`、`82a6039e…`），增量 0；`owner_version_unknown=1` |
| operator pause | 全程 `absent/false`；`pause_quiet_fault` 未记录 `PAUSED_BY_OPERATOR` |

## Memory 运行期读取（本段证据，单独标级）

- 运行期 `resident.continuity_wired=true`（上次运行记录为 false，本段首次接上）。
- `last_continuity` 为本段新鲜记录（`next_step.bound_at 15:49:34Z`）：
  `past_state=NO_ADMITTED_HISTORY`、`source_states={activity:OK, agenda:OK, memory:NO_ADMITTED_HISTORY}`、
  `llm_calls=0`、`refusals=[]`、`last_error=null`。
- 路径身份：`instance.json` 的 `NYA02_MEMORY_DB=/root/nyairo-day-20261022/one-day/life/memory/evidence.db`、
  `NYA02_MEMORY_SUBJECT=chiyo`；preflight 打开的是同一文件（evidence/controls 分离、governance healthy）。
- 标级：**READ（空库）**。可证"接线已通、本段真实读取了本实例 M0、返回空准入历史"；
  不可证"记忆内容参与选择"，也不表示她没有过去。`NO_ADMITTED_HISTORY` 不等于 `PAST_SOURCE_NOT_WIRED`。

## 具名判断：为什么不进入中断/接续

任务书第 1 段要求"必须有真动作且留下可续同一 Activity，真实材料正确"。本段四条均不满足：

1. **无真动作**：`actions=[]`，`last_action_ref=null`。
2. **无 owner 新版本**：版本增量 0，`changed.owner_versions=3` 与基线相同（历史累积，不是本段写入）。
3. **不是本段自然新开的活动**：Activity 起始于第 4 段 22:11:02，本次只是把它 `ACTIVE→PAUSED`；
   模型选的是 `CONTINUE_CURRENT` 并对其实施安全边界暂停。
4. **材料不是场景材料**：`GO_TO_OBSERVED_PLACE`（World 移动/停留），`source_refs` 是 `REST`/pose 世界事实
   与合成件 `artifact:7d39...`，不是桃花源记 `artifact:cf1428...`。

该 Activity 状态确实是 `PAUSED`、技术上可 `RESUME`，但它是旧 START 的遗留、World 目标缺失时无法执行的
移动活动，且本段没有推进任何内容。把它当作"可续合法活动"等于拿旧指针冒认断点，任务书明确禁止。
因此按停止条件停下，**不盲跑直到出现活动，也不补测试拼通过**。

## 未执行的三段及原因

- **operator pause（第 2 步）**：未写。本单没有合法活动可中断，写 pause 也无场景对象；
  `life_pause.json` 保持原状态 `absent`，本单未通过 `pause` 入口写入、也未 unlink 任何文件。
- **暂停负控段（第 3 步）**：未跑。没有 pause 位，无法验"暂停窗口 0 请求、不推进"。
- **resume（第 4 步）/ 恢复段（第 5 步）**：未跑。没有可续活动，也没有本单引入的 pause 需要撤销。

## 收尾状态

- 无活进程（ps 0 行），段内起的 Supply/World 子进程已停，lease `FREE`。
- 持久账本 `run:one-day=13`；本功能新增 1 次请求；加历史旧预检 1 次，共同 **14/96，余 82**。
- 配置仍 96/96/48/24，`run_id` 未变，没有 reset、没有新 run window。
- 未改源码、测试、notes、NOW、current.json、RUNBOOK、dispatch、权限、provider、输出 cap；
  未提交、未推送、未部署；未触碰其他实例。
- 本单唯一写面：`.agents/worker-runs/20261022-g1-interruption-exec-*` 与本报告。

## 验收命令与真实结果

worker 实跑（父级可复跑，均纯读）：

```bash
# 1. 基础只读
scripts/day_capability_probe.py status --root /root/nyairo-day-20261022        # shell 0, READY, lease FREE, writes=false
scripts/phone.py life-preflight --instance /root/nyairo-day-20261022          # shell 0, READY, blocked=[]
components/life/scripts/one_day_life.py status --instance .../one-day         # shell 0, pause absent, state idle

# 2. 一次性证据脚本（不跑 run/pause/resume，不起服务/模型）
.agents/worker-runs/20261022-g1-interruption-exec-evidence.py \
  --verify-only --root /root/nyairo-day-20261022 \
  --out .agents/worker-runs/20261022-g1-interruption-exec-verify.json
# shell 2（BLOCKED，具名），execution_complete=true, memory_read_proven=true,
# legal_activity_present=false, interruption_pass=false, continuation_pass=false
# blocked_reason=FIRST_SEGMENT_NO_RESUMABLE_LEGAL_ACTIVITY
```

`--verify-only` 的 checks 覆盖：run 与账本口径、单段 delta≤5、共同 14≤96、无 reset/新窗口、配置 96、
服务已停、无活进程、lease FREE、operator pause 原状 false、Memory 环境绑定/owner/接线/新鲜 past_state、
本段决策与活动事实。场景门槛失败只记 BLOCKED，不拿 exit 0 掩盖业务阻塞。

## 未验证

- G1 中断/接续完整场景：未开始。operator pause 窗口 0 请求、恢复后同 Activity 继续、旧 Decision 不重放均未验。
- 真模型自然新开合法阅读活动：本段未发生；"材料 ACTIVE"不等于"已有阅读 Activity"。
- 暂停负控与恢复段的真实模型行为：未验。
- 理解（understanding）仍 `UNKNOWN`；跨备份恢复安全仍 `V_C_RESTORE=FAIL`；24h soak 按用户范围未跑。
- 账本视图（probe 的 `run_window_max` 摘要）与配置 96 的差异按既有口径处理，本单未再归因。
- 全量套件、旧绿测试、notes 门禁、浏览器均未跑（任务书范围外）。
