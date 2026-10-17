# W03 回执：前台常驻推进器、持久暂停与只读运行状态

- 施工单：`D:\NYAIRO-v0.3-W03-后台推进任务单.md`
- 执行模型：国际版 WorkBuddy DeepSeek `custom/global:deepseek-v4.1-flash`（无子代理委派）
- 交付树：`D:\nyairo-v03-backend`；分支 `work/v03-w01-navigation`；HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（**未移动**）
- 状态：已实现 + 已离线测试；**未 commit、未 push、未部署、未安装/启用任何服务、未调用真实模型**
- 验收环境：WSL Ubuntu + Python 3.12.3（POSIX `fcntl`、tzdata）；`PYTHONDONTWRITEBYTECODE=1`
- 使用说明：`docs/engineering/v03-runner-usage.md`（178 行）

## 1. 开工前置与归因基线（含计数修正）

W03 开工前的 dirty 快照：`<scratch>\w03_start_head.txt`、`w03_start_branch.txt`、
`w03_start_status.txt`、`w03_start_sha256.txt`（13 个文件的 SHA256）。

**修正 W02 回执的计数**（只写在本报告，历史回执字节未改动，SHA 仍是
`docs/engineering/v03-w01-result.md 6153d03e…` / `docs/engineering/v03-w02-result.md 20316d31…`）：

- W02 回执 §10 第 1 条写"6 改 + 4 新增"；W03 开工实测是 **6 个 tracked 修改 + 5 个 untracked 文件**。
- 差的那一个新增是 **W02 回执漏计了它自己**：`docs/engineering/v03-w02-result.md`。
  实际 untracked = `chiyo_bundle/world_journey.py`、`tests/test_world_journey.py`、
  `tests/test_world_move_tool_navigation.py`、`docs/engineering/v03-w01-result.md`、
  `docs/engineering/v03-w02-result.md`（5 个，逐条见 `w03_start_status.txt`）。
- tracked 修改 6 个：`chiyo_bundle/world_move_tool.py`、`components/world/scripts/world_living_skeleton.py`、
  `components/world/scripts/world_runtime_data.py`、`components/world/scripts/world_travel_delivery.py`、
  `scripts/life_heartbeat.py`、`tests/test_world_move_tool.py`。
- 未做 reset/checkout/clean；W01/W02 未提交成果全部原样保留（下方 SHA 对照可复核）。

W03 开工时的 SHA256（节选，全表见 scratch）：

| 文件 | W03 开工（= W02 结束） | W03 结束 |
| --- | --- | --- |
| `chiyo_bundle/world_journey.py` | `a343c69aeacaa60f…` | `9194433ffc34446c…` |
| `chiyo_bundle/world_move_tool.py` | `5254e3ac5d309a6f…` | `8e54bd4c46fc8e8e…` |
| `scripts/life_heartbeat.py` | `9421566ba8452337…` | `1ac722c0e7123921…` |
| `components/world/service/world_body_gateway_api.py` | `8c35f917377cf487…` | `944e175189ddda07…` |
| `components/world/scripts/world_action_resolver.py` | `98d982266453d69e…` | `d6ecd9fe7642c8f3…` |
| `tests/test_world_journey.py` | `1be22d953ab71440…` | `6f27cab775beca4f…` |
| `components/world/scripts/world_runtime_data.py` | `5d3dfee9bc0194f5…` | `5d3dfee9bc0194f5…`（未动） |
| `components/world/scripts/world_living_skeleton.py` | `3c39cca5dafd1ea3…` | `3c39cca5dafd1ea3…`（未动） |
| `components/world/scripts/world_travel_delivery.py` | `9574f7c8a03936e3…` | `9574f7c8a03936e3…`（未动） |
| `tests/test_world_move_tool.py` | `eee1afbddfcfb535…` | `eee1afbddfcfb535…`（未动） |
| `tests/test_world_move_tool_navigation.py` | `0da2d6ff5f92d5b0…` | `0da2d6ff5f92d5b0…`（未动） |
| `docs/engineering/v03-w01-result.md` | `6153d03e4f92326b…` | `6153d03e4f92326b…`（未动） |
| `docs/engineering/v03-w02-result.md` | `20316d3132222747…` | `20316d3132222747…`（未动） |

收尾快照：`<scratch>\w03_final_snapshot.txt`（`git status --porcelain`、`git diff --check`
`exit=0`、`git diff --stat`、最终 SHA256）。W03 结束后是 8 个 tracked 修改 + 9 个 untracked
（多出的那个是 `docs/engineering/v03-w03-result.md` 本报告自身；写快照时它还不存在）。

## 2. W03 增量（相对 W02，行级）

行级增量的基准取自 W02 期的仓库副本，**每个文件都先用 SHA256 证明副本与 W03 开工快照逐字节相同**
（`<scratch>\w03_delta.sh` 输出），因此这些是纯 W03 增量：

```
新增  chiyo_bundle/world_control.py        283 行   （持久暂停控制状态）
新增  chiyo_bundle/world_runner.py        1135 行   （常驻推进器 + CLI）
新增  tests/test_world_runner.py          1030 行 / 26 项测试
新增  docs/engineering/v03-runner-usage.md 178 行
新增  docs/engineering/v03-w03-result.md   （本文件）

+53/-3   chiyo_bundle/world_journey.py            （journey_lock 公开 + 提交边界闸门 + settle_only）
+29/-69  scripts/life_heartbeat.py                 （薄包装：委托 world_runner，保留旧入口别名）
+11/-3   components/world/scripts/world_action_resolver.py （显式 environment + 目录不可读时 fail closed）
+5/-1    components/world/service/world_body_gateway_api.py （显式 environment）
+9/-2    tests/test_world_journey.py               （2 处旧断言按新语义更新并加强）
世界移动工具 chiyo_bundle/world_move_tool.py        W03 改动 6 处（见 §2.1）
```

### 2.1 `chiyo_bundle/world_move_tool.py` 的 W03 改动（6 处）

该文件的 W02 期副本已被 W02 自己的负对照回退，**无法逐字节重算行数**（不臆造数字）。
相对 HEAD 的总 diff 是 `+291/−37`（W01+W02+W03），W02 回执记录其中 W01+W02 为 `+223/−37`，
据此推算 W03 ≈ `+68/−0`。改动点：

1. `_control_module()`（懒加载 `chiyo_bundle.world_control`，不在插件启动时导入）。
2. `_continuation_note(home, env, target, world)`：中转文案**只在 `runner_status` 观测到
   `classification=="running" && health=="healthy" && !paused`** 时才承诺"后台推进器正在运行…会自动继续"，
   否则如实说"旅程已持久保存，但没有已确认在线的后台推进器，需要后续推进（run/once）才会继续"。
   这是 W02 遗留缺陷的修复：旧文案无条件承诺"到达后会继续"。
3. `_journey_result(projection, world, requested, home=None, env=None)`：新增
   `paused` / `control_blocked` / `feature_disabled` / `settle_only` 四个非终态映射（文案 + `reason_code`）。
4. `move_handler` 在目标解析之后、**任何副作用之前**加持久暂停闸门（`world_control.gate`），
   暂停/坏控制状态一律拒绝，**即时模式（关闭计时）同样拒绝**。
5. blocked 文案统一：工具不会自动替换被阻塞的旅程，明确"只有低层显式 `accept_journey` 能归档旧旅程并开始新目标"。
6. 两处 `_journey_result` 调用点补传 `home, env`（让中转文案能只读探测 runner）。

### 2.2 实施中由测试暴露并修正的 4 个产品缺陷

1. `world_runner.classify_journey_result` 把 `feature_disabled` 误判成 `degraded`
   （只匹配了 `"disabled"`）→ 修正为 `{"disabled", "feature_disabled"}`。
2. 没有活动旅程时 `tick` 只报 `idle`，即使控制状态已暂停/损坏 → `tick` 在分类为 `idle` 时补查只读闸门，
   如实报 `paused` / `control_blocked`（避免"暂停中却显示闲着呢"）。
3. 控制状态损坏（`control_blocked`）原先被当作健康 → 新增 `_UNHEALTHY_TICK_STATUSES`
   （`degraded`/`severe`/`control_blocked`），统一用于常驻循环的退避与 `consecutive_degraded`、
   `status` 的 `health`/`reason_codes`、以及 `once` 的 `ok`/退出码（`control_blocked` → 退出码 1）。
4. `world_action_resolver._m15_destination_verdict` 在目录不可读时会把 `WorldDataError` 抛给调用方 →
   改为 fail closed 返回 `UNKNOWN_DESTINATION`（与同模块其它处理器一致），零参兼容保留。

## 3. 装配与单实例（run lease）

- 装配全部显式：`--world-home`（必填、绝对、必须已初始化且在源码树外）、`--world-package`、
  `--env KEY=VALUE`（可重复）。运行环境**不继承**进程里的世界配置（四个世界键先剔除再填回）；
  `CHIYO_WORLD_HOME`/`HERMES_HOME` 指向别处 → `ENVIRONMENT_IDENTITY_CONFLICT`；
  包冲突 → `ENVIRONMENT_PACKAGE_CONFLICT`。
- 身份不靠猜：`validate_world_home` 用 `world_state.json` × 活动世界包交叉校验
  world_id + timezone（`WORLD_HOME_IDENTITY_MISMATCH`）。**所有失败路径都零创建**
  （缺目录/非目录/未初始化/在源码树内/包不可解析 → 退出码 2，且不产生目录、锁、状态文件）。
- 不无条件强制行为特性：`build_environment` 只设 `CHIYO_WORLD_HOME` + 显式 `--env`/`--world-package`；
  feature gate 必须显式给出（W02 的心跳测试因此显式传 gate）。
- run lease：`<world_home>/data/world/world_runner.lock` 上的 `flock(LOCK_EX|LOCK_NB)`，
  被占用立刻 `RUN_LEASE_HELD`（退出码 4），**不等待**；进程死亡由内核释放，
  不删锁文件、不把 PID 当归属证明。
- 锁序（全局唯一，禁止反向）：`world_journey.json.lock → world_state.json.move.lock →
  world_travel_delivery.json.lock`。**run lease 与这三把锁完全独立、不嵌套**；
  `status`/`pause`/`resume` 不获取 lease，所以 runner 在跑时控制命令不会被反压成死锁
  （E 组测试里正是 runner 持锁时调用 `pause`/`status` 并成功）。
- 退出清理在**持有 lease 期间**完成（`with run_lease(...) as lease: run_forever(...)`），
  因此本进程的停止记录不会覆盖后来重启进程的记录。

## 4. 持久暂停语义与线性化点

- 语义：暂停 = **不接受新移动、不启动新段**；已经真正出发的那一段仍按真实时间到达并确认
  （结算不是"新移动"），下一段留待 `resume`。不冻结在途、不倒退位置、不重置 `expected_arrival_at`。
- **线性化点**：`world_control.set_paused` 的写入与 `_step`/`accept_journey` 的闸门读取
  **在同一把 journey 锁内**（`world_runner.set_pause` → `world_journey.journey_lock(home)`）。
  于是"`pause` 返回成功"之后不可能再提交新段；若已有提交在进行，`pause` 等它做完再确认
  （这就是并发测试里"advance 先赢"的合法次序：只多提交一段，且那一段是 pause 确认之前就已线性化的）。
- 闸门在**引擎提交边界内**，不是循环入口检查一次：`_step` 的 `not_started` 分支在
  `_submit_segment` 之前查闸门；`accept_journey` 在创建旅程之前查闸门。
  因此模型工具、`accept_journey`、`advance_journey`、`once`、即时模式都绕不过去。
- 拒绝是**非终态**：`paused`/`control_blocked` 不写旅程账本、不改目标、不标 blocked，
  修复/恢复后同一次调用链可以继续。
- `control_blocked`（控制状态损坏/读失败）= **fail closed**：绝不默认"未暂停"；
  `absent`（无暂停记录，文档化默认=未暂停）与 `corrupt` 在 status 里分开报告。
- 显式 `settle_only`（`advance_journey(settle_only=True)` / `--settle-only`）：只结算已出发段、
  绝不发起下一段；单独传 `allow_start=True` 不能覆盖持久暂停闸门。
- 禁用 feature（`CHIYO_WORLD_TRAVEL_TIME_ENABLED=0`）与暂停是**两个不同状态**，
  status 分开报告；gate 关闭时同样不启动新段、不瞬移，且改为**非终态** `feature_disabled`
  （W02 的 `test_tool_flag_off_never_teleports_active_journey` 期望值随之从 blocked 终态改为
  `feature_disabled`，原保障"不瞬移、不越权写 owner"逐条保留并加强）。

## 5. 只读 status 与健康

- `runner_status(home, environment=…)` 零创建、零写入、不写 mtime、不调用 `realize`/`read_move_state`、
  不结算到达。缺目录/无记录/坏记录都如实分类，不创建默认状态。
- `classification`：`running`（记录 running + lease 被真实持有）/ `stale`（lease 被持有但
  `last_tick_at` 超过 `stale_after_seconds`）/ `stopped`（正常退出记录）/ `crashed`（记录仍 running 但
  lease 已释放）/ `not_running`（无记录）/ `unknown`（记录损坏）。
- `liveness` 用**在已存在锁文件上的无创建探测**（`open("r")` + `LOCK_EX|LOCK_NB`）得到
  `held/free/unknown`；**PID 只作展示**，`pid_is_liveness_proof: false`。
- `health` 与旅程结果分开；`degraded`/`severe`/`control_blocked` 一律不显示健康。
- 输出脱敏：不含完整 environment、密钥、真实配置原文；测试用 canary 验证
  `CHIYO_WORLD_PACKAGE` 与注入的 `CHIYO_TEST_CANARY` 都不出现在 JSON 里。
- 不承诺原子快照：各字段来自各自读到的版本（`journey.revision`、`checked_at` 是权威）。

## 6. 统一装配的剩余环境调用点（§6）

- `components/world/service/world_body_gateway_api.py`：`_reachable_move_candidates` 改为
  `wls.location_catalog(self.service.config.environment)`（+5/−1，零参兼容保留）。
- `components/world/scripts/world_action_resolver.py`：`_m15_destination_verdict(..., environment=None)`
  → `skeleton.location_catalog(environment)`，并在目录不可读时 fail closed（+11/−3）。
- 两处都**没有**修改全局 `os.environ`；定向回归见 §7 第 6 项与 §9 H 组。
- 工具中转文案按 §2.1 第 2 条改为"跟随真实 runner 观测"。

## 7. 实际执行命令 / 退出码 / 计数

全部在 WSL Ubuntu、`PYTHONDONTWRITEBYTECODE=1`、`TMPDIR=/tmp`、离线执行
（脚本：`<scratch>\w03_final_regression.sh`，原始输出 `w03_final_regression.txt`）：

| # | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| 1 | `cd tests && python3 -m unittest discover -s . -p test_world_journey.py` | **28 tests / OK** | 0 |
| 2 | `cd tests && python3 -m unittest discover -s . -p test_world_move_tool.py` | **16 tests / OK** | 0 |
| 3 | `cd tests && python3 -m unittest discover -s . -p test_world_move_tool_navigation.py` | **14 tests / OK** | 0 |
| 4 | `python3 -m unittest components.worldgen.tests.test_v04_navigation test_edited_world_runtime test_runtime_wiring` | **24 tests / OK** | 0 |
| 5 | `cd components/world/service && python3 -m unittest discover -s . -p 'test_*.py'` | **259 tests / OK** | 0 |
| 6 | `cd tests && python3 -m unittest discover -s . -p test_world_runner.py` | **26 tests / OK**（W03 新增） | 0 |
| 7 | `cd tests && python3 -m unittest discover -s . -p 'test_*.py'`（整仓） | 158 tests / **FAILED(errors=9)** | 1 |

- 整仓 132（W02 基线） + 26（W03 新增） = **158**。9 个 error **全部**是
  `test_update.UpdateTransactions.*`（`scripts/update.py:219` 缺 `psutil`，即未跑
  `scripts/install.sh` 的环境前提错误），与本次改动无关；按施工单**没有**运行 install 去修。
- 另有 3 次重复运行 `test_world_runner`（含并发线性化测试单独 6 次）确认无 flaky；
  新增测试运行期间无 `ResourceWarning`。
- CLI 退出码实现（`world_runner.EXIT_*`）：0 成功（含 `paused`）、1 拒绝/降级
  （`degraded`、`control_blocked`）、2 用法/校验（零创建）、3 严重（`JOURNEY_STATE_CORRUPT`）、
  4 lease 被占。错误 JSON 走 stderr，stdout 只承载结果。

## 8. 负对照证据

副本：`<scratch>\w03-neg`（`tar` 复制工作树，排除 `.git`/`__pycache__`，191 MB）。
`<scratch>\w03_neg_revert.py` **只回退三处 W03 闸门接线**（`_step` 的 not_started 闸门、
`accept_journey` 的闸门、`move_handler` 的闸门），**保留** `world_control.py`、`world_runner.py`、
`journey_lock` 与全部依赖 —— 因此失败不可能是 ImportError。

- `diff -r -x .git -x __pycache__ -q` 证明副本与工作树**只差那两个文件**
  （`chiyo_bundle/world_journey.py`、`chiyo_bundle/world_move_tool.py`）。
- 副本里 `tests/test_world_runner.py`：**26 项中 4 项失败**（真实行为差异，非导入错误）：

```
FAIL test_pause_allows_started_segment_to_arrive_but_not_next   AssertionError: 'started' != 'paused'
FAIL test_pause_linearizes_against_concurrent_advance           AssertionError: 'started' != 'paused'
FAIL test_tool_once_and_instant_mode_cannot_bypass_pause        AssertionError: True != False
FAIL test_corrupt_control_state_blocks_new_segments             AssertionError: 'started' != 'control_blocked'
```

- 同一副本里 `tests/test_world_journey.py`（W02 的 28 项）**仍然全绿**，
  说明副本除这三处回退外与工作树等价、且回退没有破坏 W02 既有语义。
- 因此：**暂停闸门/线性化/工具与 once 不得绕过**这些新测试对 W02 前置存在真实行为失败，
  在 W03 实现下通过。原始输出：`<scratch>\w03_neg_result.txt`。

## 9. 新增测试覆盖（`tests/test_world_runner.py`，26 项，按施工单 §7 分组）

| 组 | 类 / 项数 | 覆盖要点 |
| --- | --- | --- |
| A | `RunLoopTest` 1 | 无新聊天输入，注入 clock 的常驻循环把真实三段旅程推到终点；每段只启动一次（travel `execution_id` 与 segment 一一对应）、真实位置/owner 记录一致、`world_revision` 单调 |
| B | `RunLeaseTest` 3 | 两个**真实子进程**竞争 lease：恰好一个成功、另一个退出码 4 且不等待；SIGTERM 后 lease 释放且可重启；`kill -9` 后 lease 可恢复、status 报 `crashed`（不删锁文件） |
| C | `PauseSemanticsTest` 2 | 暂停后已出发段到期只确认本段、不发下一段；run 重启仍 `paused`；`resume` 继续且不重走已确认段；无旅程/终态旅程下 pause/resume 不伪造移动；`settle_only` 只结算 |
| D | `PauseSemanticsTest` 4 | **屏障同步**的 pause × advance 并发线性化（两种次序都不得在 pause 确认后启动新段）；工具（计时 + 即时两条路径）、`accept`、`advance`、`once` 都绕不过；**坏控制状态 fail closed**（含常驻循环的退避与"不算健康"） |
| E | `RunnerStatusTest` 5 | 缺目录/无 runner 零创建零写入；反复读取 + 过到期时间零状态写（快照与 mtime 逐字节比较）、不结算到达；running/paused/stopped/crashed 分类；陈旧记录与**伪造 PID** 不显示健康；坏记录 `unknown`；输出脱敏 |
| F | `RunnerRefusalTest` 5 | 非法 world_home（缺/非目录/未初始化/源码树内/身份不匹配）零创建拒绝；身份与包冲突拒绝；interval `nan/inf/负数/0.001/abc` 与 `run --now`、`--max-ticks 0` 拒绝且不创建记录/锁；`once --now` 格式与时区校验；feature gate 关闭不启动新段且非终态 |
| G | `RunnerDegradedTest` 3 | 坏旅程账本 → `severe` + 退出码 3 + 原字节保留；坏 travel 记录 → `degraded` + 有限退避（`interval×2^n`，单次等待 ≤0.25s 片）+ 不清理不新建、不假健康；图变更 → `degraded` 且目标保留 |
| H | `EnvironmentWiringTest` 2 + `ToolContinuationNoteTest` 1 | gateway/resolver 在"进程环境指向坏包"时仍用显式请求环境（并给出零参回落的 fail closed 行为）；旧汐见市（无世界包）兼容；工具中转文案只在观测到健康在线 runner 时才承诺后台续行 |

测试同步：`tests/test_world_journey.py`（+9/−2）里 2 条旧断言按新语义更新，
逐条保留了原保障：`test_tool_flag_off_never_teleports_active_journey` 期望从 blocked 终态
改为 `feature_disabled`（仍断言不瞬移、位置/owner 零写入、可恢复）；
`test_one_shot_advance_only_continues_existing_journey` 两处心跳调用显式补
`--env CHIYO_WORLD_LIVING_SKELETON_ENABLED=1 --env CHIYO_WORLD_TRAVEL_TIME_ENABLED=1`
（runner 不再隐式继承进程环境，测试改为显式给 gate）。

## 10. 已知边界与未闭合项

1. **没有真实长时 soak**：常驻循环的自动化覆盖用注入 clock/sleeper/utcnow，
   没有跑几十分钟 wall-clock；"长期稳定"不在本单结论里。
2. 常驻循环需要 POSIX（`fcntl`）；原生 Windows 只能只读 `status`（lease 证据 `unknown`）。
3. `pause` 只拦"新段"，不撤销已在途的物理移动；位置与到达时间仍由 owner 投影决定。
4. 控制状态损坏时**只能人工处理**（不提供自动修复/清空/重建）：本单明确不新增
   cancel / 强制清空 / 自动恢复 blocked 的 API。
5. blocked 旅程仍由工具拒绝并如实说明"只有低层显式 `accept_journey` 能归档并换目标"；
   本单没有把工具升级成自动替换（施工单允许的最小方案）。
6. `world_move_tool.py` 的 W03 行级增量无法逐字节重算（W02 期副本已被 W02 负对照回退），
   只能给推算值 `≈+68/−0`（见 §2.1）。
7. 未安装 systemd/计划任务/daemon，未注册自启动；使用说明里给出的 unit 片段标注"未安装"。
8. 整仓 `tests/` 的 9 个 `test_update` error 是环境前提（缺 `psutil`/未跑 install），
   本单按施工单不修、不跑 install。

## 11. 范围外写入

- 工作区内只写：`chiyo_bundle/world_control.py`、`chiyo_bundle/world_runner.py`、
  `tests/test_world_runner.py`、`docs/engineering/v03-runner-usage.md`、
  `docs/engineering/v03-w03-result.md`（新增）；以及 §2 列出的 6 个既有文件（含 2 个 W02 期新增文件）。
- **没有**修改 W01/W02 回执字节，**没有**删除任何用户文件，**没有**改动全局环境/依赖/真实 profile/密钥/供应商。
- 临时脚本、负对照副本、日志、快照全部在会话 scratch
  （`<scratch>\w03-neg`、`w03_neg_*.sh/py/txt`、`w03_*_regression.txt`、`w03_final_snapshot.txt` 等）。
- 未 commit、未 push、未部署、未装服务；完成后停止，未开始 W04/配置/长测。
