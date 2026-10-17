# 世界 / 每日测试确定性修复结果（窄修复，只改 3 个测试文件）

> 本包**没有**产品改动，也**不算**产品进度。它只修三个测试自身的墙钟与候选顺序依赖，
> 让同一份未提交产品树上的红灯变成可复现的绿，断言一条都没放宽。
> 原始事实与本包无关性判定见 [world-daily-i-result.md](world-daily-i-result.md) §7（只读，未改）。

## 0. 环境与纪律

- repo `/mnt/d/nyairo-v03-backend`（Windows `D:\nyairo-v03-backend`），分支 `work/v03-w01-navigation`，
  HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`。valuable dirty 全部保留，未 stage / commit / reset / clean。
- venv `/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`（3.12.3），
  `PYTHONDONTWRITEBYTECODE=1`、`TMPDIR=/tmp/nyaw05r.jdK3q`、repo cwd，scratch runner `i_daily_runner.py`
  （**只**把 `shutil.rmtree` 换成 no-op 以保留夹具，断言一字未改）。
- 只跑**精确 test id**，不跑模块/类（继承会带进几十个旧用例）。
- 产品文件、既有他人 notes、看板、管理脚本一律未动；只改下面 3 个测试文件与 2 个本项目独有产物。
- 日志 / 临时夹具唯一落在 scratch `.../daily-test-determinism/` 与 `/tmp/nyaw05r.jdK3q/test-det-*`，一个都没删。

## 1. W：`test_closed_door_is_allowed_in_timed_travel_too` 的真实墙钟漂移

**位置**：`tests/test_world_daily_opportunities.py`（`position()` 辅助 167-174 行；用例 325-357 行）

**根因**（产品只读核实，未改）：`world_living_skeleton.read_move_state(now=None)`
（`components/world/scripts/world_living_skeleton.py:744-758`）在 travel 打开时**先**调
`world_travel_delivery.realize_due_travel(now=None)`，即用**真实墙钟**结算在途行程。用例注入的时钟是
`2026-10-17 13:00 JST`（`04:00Z`），咖啡店段 `expected_arrival_at = 2026-10-17T04:05:30Z`；真实 UTC 一旦越过
这个点，"尚未到达"的行程就被判到期并写进 owner 的 `world_state.json`，于是断言里的位置从 `street_corner`
变成 `cafe`。owner 自己的 `world_state.json` 原本仍是 `street_corner`（`i-daily-i-result.md` §7.1 的探针证据）。

**修法**：`position()` 增加 `now=` 透传（默认仍 `None`，其它用例行为不变），计时旅程用例的三处读取
（336 / 349 / 355 行）改传用例自己的 `now`。`realize_due_travel` 在 `instant < expected_arrival_at` 时
只返回 `in_progress` / `ARRIVAL_NOT_DUE`、不写盘，所以这是**纯读**，与"观察只读"的契约一致。

**没有放宽**：出发 ≠ 到达（349 行 before-arrival 断言原样保留）、闭店可走到门口
（`destination_availability == CLOSED`）、`inside` 无 affordances（355-357 行）、以及真实到达后才变 `cafe`
（355 行）全部保留。

## 2. W05R：`test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset` 的恒等变换补丁

**位置**：`tests/test_one_day_life_w05r.py:1389-1433`

**根因**：用例把 `lrb._utc_day` patch 成固定 `"2026-10-17"`。而
`LifeRequestBudget._active_day` 恒为 `max(stored_day, _utc_day())`
（`components/life/scripts/life_request_budget.py:263-269`），`reserve(now=…)` 的 `now` 只进 token 的 `at`
时间戳（355 / 364-365 行），落账当日始终来自 `_active_day`。于是当真实 UTC 日期**就是** 2026-10-17 时，
这个 patch 是恒等变换，"新的一天"分支不可达，仍然按当天 2/2 上限拒绝
（`REQUEST_BUDGET_UTC_DAY_LIMIT`，`spent 2 of 2`）。

**修法**：整条用例自带时钟，只从预算自己的账本读一次 `initial_day`
（`budget.view()["utc_day"]`，不写死任何日期），据此算出 `next_day` / `prev_day`；三段分别在这些显式
`_utc_day` 补丁下跑：初始日两次预留 + 拒绝、下一日允许 + 预留 + history、回拨日前一日的只读 view 与再预留。

**没有放宽，并且加严**：跨日 reset（`allowed=True`、`utc_day==next_day`、`utc_day_calls==1`、history 两天都在）、
回拨不 reset（`utc_day==next_day`、`utc_day_calls==1`、history 不含 `prev_day`）、上限、持久化（每次 `view()`
都重新读盘）全部保留；另外新增"回拨也不**返还**额度"：回拨日再花一次即到 2/2，且 `check()` 不允许。

## 3. D-final：`test_life_v1_restart` 负控依赖候选顺序

**位置**：`tests/test_life_v1_restart.py`（新 `StartControlLoopback` 136-188 行；控制轮 269-281 行；
父进程断言 402-407 行）

**根因**：负控原来换回 `StubCognition("artifact")`，它的选法是"非世界候选里的**第一个**"再强发 `START`
（`tests/test_one_day_life.py:629-638`）。同窗口里既有 `CONSIDER_PERSONAL_OPPORTUNITY`（legal
`[DEFER, START]`，就是原 artifact 的新 head），也有 `CONTINUE_CURRENT`（legal
`[DEFER, CONTINUE, PAUSE, ABANDON]`）。候选 id 每次运行都不同、表顺序不稳定，一旦那个只许
CONTINUE/PAUSE 的候选排在前面，AG-1 就先以
`INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE` 拒掉，AG-2 的
`CANNOT_START_WHILE_FOREGROUND_ACTIVE` 根本测不到——负控变成 `DECISION_FAILED`。

**修法**（不改 runtime、不放宽断言）：
1. 新 `StartControlLoopback(WORK)`：只从 `legal_decisions` 含 `START` 的候选里挑，并优先挑
   **`target_refs`/`source_ref` 命中原 artifact** 的那个；挑不到合法 START 就当场 `AssertionError`
   并打印整窗候选，绝不静默降级成别的决策。
2. 控制轮的适配器换成它，并把 `<候选 id, kind, legal, about_work, 整窗>` 记进
   `report["start_control_candidate"]`。
3. 父进程新增三条断言：所选候选的 `legal` 含 `START`、`about_work` 为真、`start_control["decision"] == "START"`。
   于是这条负控证明的是"**AG-1 允许的 START 在同一窗口被 AG-2 拒**"，而不是"AG-1 先拒了"。

**没有放宽**：`outcome == ADOPTION_REJECTED`、`adoption_status == REJECTED_STALE`、
`detail` 含 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`、`head` 不变、Activity 仍 `ACTIVE` 且 revision 不变、
正路径 A `START` / B `CONTINUE` 同一 `activity_ref` + 新 version、budget run-window 与跨进程 run identity 全部原样。

## 4. 修复前的红灯（原文）

命令（scratch `run_case.py`，`subprocess` 取真实 returncode；日志 `pre-*.log`）：

```
venv/bin/python .../i_daily_runner.py \
  tests.test_world_daily_opportunities.DailyNeighborhoodTest.test_closed_door_is_allowed_in_timed_travel_too \
  tests.test_one_day_life_w05r.RequestBudgetTest.test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset
```

`pre-w-budget-seed7.log`，**returncode 1**，`Ran 2 tests … FAILED (failures=2)`：

```
FAIL: test_closed_door_is_allowed_in_timed_travel_too (tests.test_world_daily_opportunities.DailyNeighborhoodTest.test_closed_door_is_allowed_in_timed_travel_too)
  File "/mnt/d/nyairo-v03-backend/tests/test_world_daily_opportunities.py", line 343, in test_closed_door_is_allowed_in_timed_travel_too
    self.assertEqual(self.position(env=env), "street_corner")
AssertionError: 'cafe' != 'street_corner'

FAIL: test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset (tests.test_one_day_life_w05r.RequestBudgetTest.test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset)
  File "/mnt/d/nyairo-v03-backend/tests/test_one_day_life_w05r.py", line 1400, in test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset
    self.assertTrue(allowed["allowed"], allowed)
AssertionError: False is not true : {'allowed': False, 'reason_code': 'REQUEST_BUDGET_UTC_DAY_LIMIT', 'detail': 'utc day 2026-10-17 spent 2 of 2', 'utc_day': '2026-10-17', 'utc_day_calls': 2, 'run_window_calls': 2}
```

`pre-restart-seed12345.log`，**returncode 1**，`Ran 1 test … FAILED (failures=1)`：

```
    self.assertEqual(control["outcome"], "ADOPTION_REJECTED", control)
AssertionError: 'DECISION_FAILED' != 'ADOPTION_REJECTED'
- DECISION_FAILED
+ ADOPTION_REJECTED
 : {'outcome': 'DECISION_FAILED', 'decision': None, 'adoption_status': None, 'detail': 'INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE', 'llm_calls': 1}
```

同一条用例 `PYTHONHASHSEED=7` 在修复前**这一次**是 **returncode 0**（`pre-restart-seed7.log`），
说明它不只是种子依赖，顺序本身在两次运行之间就会变；`i-daily-i-result.md` §7.3 记录的
`1 → 0 / 7 → 失败 / 12345 → 失败 / 987654 → 0` 与本次观察一致：**红绿随候选顺序漂移**。

顺序依赖的窄局证明（scratch `demo_old_control_is_order_dependent.py`，直接调真实
`StubCognition("artifact")`，只换候选顺序，不碰产品）：

```
order=START-legal first      picked=cand:28687dbef726d55b kind=CONSIDER_PERSONAL_OPPORTUNITY legal_decisions=['DEFER', 'START'] START_legal=True
    -> AG-1 verdict: accepted, reaches AG-2
order=CONTINUE-only first    picked=cand:6b8cf006079e00e6 kind=CONTINUE_CURRENT legal_decisions=['DEFER', 'CONTINUE', 'PAUSE', 'ABANDON'] START_legal=False
    -> AG-1 verdict: INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE (negative control lost)
```

整窗候选来自只读探针 `probe_control_window_seed.py`（在内存里给 WORKER 的控制轮加记录，不写 repo；
`PYTHONHASHSEED=12345`，`probe-control-window-seed12345.log`）：控制轮四件候选依次是
`CONSIDER_PERSONAL_OPPORTUNITY`（关于原 artifact 新 head，legal `[DEFER, START]`）、
`CONTINUE_CURRENT`（legal `[DEFER, CONTINUE, PAUSE, ABANDON]`）、另一件阅读产物的
`CONSIDER_PERSONAL_OPPORTUNITY`、地点的 `CONSIDER_WORLD_OPPORTUNITY`；负控最终
`ADOPTION_REJECTED / REJECTED_STALE / CANNOT_START_WHILE_FOREGROUND_ACTIVE:<activity>`。

## 5. 修复后的命令与真实 exit

`run_case.py`（scratch）：`subprocess.run([venv, i_daily_runner.py, <ids…>], cwd=repo,
env={PYTHONDONTWRITEBYTECODE=1, TMPDIR=/tmp/nyaw05r.jdK3q, PYTHONHASHSEED=<seed>})`，
returncode 由 `subprocess` 直接取，不走 pipeline。runner 仍是 `i_daily_runner.py`（禁用物理清理）。

| 命令（精确 id） | seed | returncode | 结果 |
|---|---|---|---|
| W `…DailyNeighborhoodTest.test_closed_door_is_allowed_in_timed_travel_too` + 预算 `…RequestBudgetTest.test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset` | 7 | **0** | `Ran 2 … OK`（`final-w-budget-seed7.log`） |
| `tests.test_life_v1_restart.RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`（单条精确 id，逐个 seed 各一次） | 7 | **0** | `Ran 1 … OK`（`final-restart-seed7.log`） |
| 同上 | 1 | **0** | `Ran 1 … OK`（`final-restart-seed1.log`） |
| 同上 | 987654 | **0** | `Ran 1 … OK`（`final-restart-seed987654.log`） |
| 同上 | 12345 | **0** | `Ran 1 … OK`（`final-restart-seed12345.log`；修复前该 seed 为 `DECISION_FAILED`） |
| 同上 | 0 | **0** | `Ran 1 … OK`（`final-restart-seed0.log`） |
| 同上 | random | **0** | `Ran 1 … OK`（`final-restart-seedrandom.log`） |
| 同文件兄弟用例 `…DailyNeighborhoodTest.test_closed_cafe_door_is_walkable_without_store_affordances`（确认 `position()` 助手改动没有波及其它读取方） | 7 | **0** | `Ran 1 … OK`（`sibling-w-closed-door-seed7.log`） |

矩阵整体：`restart_matrix.sh` → 6/6 returncode 0，`MATRIX_RC=0`。

失败→通过的核心对照：同一条 restart 用例在 `seed=12345` 修复前 `DECISION_FAILED`（§4），修复后 `OK`；
负控细节变成 `ADOPTION_REJECTED / REJECTED_STALE / CANNOT_START_WHILE_FOREGROUND_ACTIVE`。

## 6. 剩余限制（不夸称）

1. **并行写集干扰**：本包跑矩阵时，`components/life/scripts/autonomous_life_cycle.py` 正被 Life 核心
   writer 逐段重写（观测到 14:07:34、14:08:14 两次 mtime 变化），中途出现过
   `SyntaxError: unmatched ')'`（该文件 2298 行附近）与 `KeyError: 'cycle'`（worker 里
   `assembled["cycle"]`）等**产品瞬时半成品**导致的失败，与测试修复无关（日志 `post-restart-matrix-seed0.log`、
   `post-restart-matrix-seedrandom.log`、`post-restart-matrix-seed12345.log`）；等 writer 落定后重跑才作数。
2. 本包**没有**跑整模块或类级回归（继承会收进数十个旧用例），只跑上面列出的精确 id。
3. 三条用例仍然真实依赖产品语义（AG-0 候选来源、AG-2 采纳规则、travel owner），产品若再改这些契约，
   本包不做担保，需重新点测。
4. `test_life_v1_restart` 的负控仍然要求"该窗口里存在一个 START 合法的候选"。若将来 AG-0 不再为
   已完成版本的作品提供 START 机会，新分支会**当场报错并打印整窗候选**（不是静默变弱），届时需要按当时的
   真实候选重新设计负控。
