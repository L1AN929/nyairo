# W05 回执：一天自主生活最小闭环

- 候选树：`D:\nyairo-v03-backend`，分支 `work/v03-w01-navigation`，HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（**未移动、未 commit**）。
- 开工状态：`w05_start_manifest.txt`（20 项 dirty：8 tracked M + 12 untracked）。W01–W04 成果与历史回执字节未动。
- 本单**不启动真实 24h、不接真实模型、不 commit/push/部署、不装服务**。

## 1. 实际接口图（本树真实调用点）

| 环节 | 位置 | 事实 |
| --- | --- | --- |
| 前台常驻 | `components/life/scripts/autonomous_life_resident.py` | 一个 state root、一个 runtime、一个循环；canonical writer lease = 互斥 |
| 一跳 | `autonomous_life_assembly.runner_tick()` | poll 事件 feed → `runner.run_due(act)` |
| 决策链 | `autonomous_life_cycle.evaluate_once()` → `runtime.coordinator.run_decision_and_adoption_cycle` | AG-0 材料 → AG-1 决策 → AG-2 采纳 → AR-0 动作 |
| 世界观察 | `world_move_opportunity_source.WorldMoveOpportunityProvider` | 只喂 selector 可见的 MOVE 候选 |
| 世界动作（W05 改） | `world_journey_action_adapter.JourneyWorldResolver` → `chiyo_bundle/world_journey.accept_journey` → `world_living_skeleton.apply_move_to` + `world_travel_delivery` | **计时出发**，位置权威仍是唯一 `world_state.json` 写者 |
| 世界结算（W05 新） | `autonomous_life_cycle.settle_world_journey()` → `world_journey.advance_journey` | 真实时间到达；只有 owner 观测到目的地才认领到达 |
| 作品 | `life_artifact_port` + `managed_artifact_action_adapter` + `model_continuation_writer` | Supply grant/CAS/head/receipt；head 与内容真的读出来 |
| 生活暂停（W05 新） | `components/life/scripts/life_pause.py` | `life.pause.v1`，原子替换；损坏 = fail closed |

**同一 owner / 同一 state home 的证据**：世界位置权威写 `<home>/data/world/world_state.json`
（`world_living_skeleton.py:139-148`、`world_action_resolver.py:522-547`），旧 World 服务读**同一个**
`config.world_state_path`（`world_body_config.py:65,137-138`、`world_body_gateway_api.py:190`）；
W04 旅程账本写 `<world_home>/data/world/world_journey.json`（`world_journey.py:223-237`）。
`world_body_config` 把自己的 `world_home` 同时导出为 `HERMES_HOME`（`:174-175`），所以两者指向同一目录：
**生活侧没有第二套位置镜像**，锁序仍是 `journey → world → travel`。

## 2. 修改范围（本单新增/改动，全部未提交）

新增：
- `components/life/scripts/life_pause.py` —— 生活侧持久暂停位（`life.pause.v1`，`absent/ok/corrupt` 三态，损坏 fail closed）。
- `components/life/scripts/world_journey_action_adapter.py` —— AR-0 世界槽位的**旅程** resolver
  （`JourneyWorldResolver` / `RealJourneyWorldActionAdapter`），提交的是计时出发，回执带 `arrived`。
- `components/life/scripts/one_day_life.py` —— 一天入口 CLI：`prepare / preflight / run / status / pause / resume / report`。
- `tests/test_one_day_life.py` —— 17 项（owner 级 / cycle 级 / 报告判读）。
- `docs/engineering/v03-w05-result.md`（本文件）、`docs/engineering/v03-one-day-runbook.md`、
  `D:\NYAIRO-v0.3-24小时自主生活启动方案.md`。

改动：
- `autonomous_life_cycle.py`：新增 `OUTCOME_IN_TRANSIT` / `OUTCOME_PAUSED_BY_OPERATOR`、`journey_module()`、
  构造参数 `world_home` / `pause_state_root`、`pause_gate()`、`_observe_position()`、`settle_world_journey()`；
  `_classify_world_step` 在 `details["arrived"] is False` 时**不再**给 LR-5 目的地观测、不再报 `ACTED`。
- `autonomous_life_resident.py`：`set_paused()`、`resolve_world_action_adapter()`（计时开启时用旅程 resolver，
  关闭时保持原即时路径）、每 tick 先 `settle_journey()`、`journey_view()`、summary 增 `pause`/`journey`、
  CLI 增 `--pause/--resume/--reason`。
- `autonomous_life_runner.py`：`WAKE_IN_TRANSIT` / `WAKE_HELD` 与 `OUTCOME_CLASS` 两个映射。
- `action_reality_ledger.py`：receipt `details` 透传 resolver 自己的 detail 块（否则"出发≠到达"这个事实到不了上层）。
- `one_day_life.py` 自身：`run()` 前置 `root=Path(root)`、state root `0700`、`prepare()` 通过既有控制面
  `control.set_enabled()` 持久打开自主开关、`prepare()` 用 `create_artifact` 直接写 operator 种子版本。

## 3. 逐能力实现等级与测试

| 能力 | 等级 | 证据 |
| --- | --- | --- |
| 一天入口（prepare/preflight/run/status/pause/resume/report） | **实测通过（进程级）** | `w05_cli_rehearsal.json`（真子进程 + 真 owner + loopback 替身模型，31.2s） |
| 两种合格选择驱动真实 owner | **实测通过** | 世界：`test_world_choice_departs_without_arriving...`；作品：`test_artifact_choice_commits_a_real_supply_version`（真实版本号 +1、内容读回） |
| NO_ACTION / DEFER 零副作用 | **实测通过** | `test_no_action_and_defer_change_nothing`（无新版本、无旅程文件、无 action 记录、0 次内容生成） |
| 观察外目标被拒 | **实测通过** | `test_unobserved_target_is_refused_without_touching_world_state`（`LOCATION_NOT_REACHABLE`，`world_state.json` 字节不变） |
| 计时在途不提前完成 | **实测通过** | 同一用例：出发后位置仍在起点、`progress_state` 为空；到期前结算 `arrived=False`，到期后才 `completed` |
| 下一次选择受真实结果影响 | **部分** | 作品 head/旅程历史/已确认活动都进下一轮材料与决策上下文（cycle 用例第二轮读到新 head）；未做"结果→不同选择"的 A/B 对照 |
| pause / resume 边界 | **实测通过** | `test_pause_holds_the_window_without_any_model_call`（暂停窗 0 次模型调用、0 新段；恢复后立刻又能出发）+ `test_pause_is_the_life_bit_only_and_status_is_read_only`（世界暂停位未被误触） |
| 停机不补经历 | **实测通过** | `test_a_stopped_process_is_not_backfilled`（第二跳 `NOT_DUE`）+ 报告缺口判读 |
| 双 writer 拒绝 | **实测通过** | `test_second_writer_is_refused`（`ActivityLockError` + 入口 exit 4） |
| 只读 status 零字节变化 | **实测通过** | 整棵实例树 (size, sha256, mtime_ns) 快照前后一致 |
| UNKNOWN 对账不重放 | **部分** | `reconcile_request` 按 request key 精确对账（`SUCCEEDED`/`NOT_FOUND`，段级查询仍可用）；未构造真实 AR-0 UNKNOWN 动作 |
| 预算：全部调用计数 / 超限 / 跨 UTC 日 / 重启 | **部分** | 复用既有 `RoundBudget`（UTC 日持久化、cognition 计数），cycle 用例读到了 used/remaining；未新增专项用例 |
| 24h 报告不提前 PASS | **实测通过** | 4 项报告判读用例（覆盖不足→`NOT_PASS`；满窗零动作→`NOT_PASS`；满窗+确认结果→`PASS`；满窗+停机→`INTERRUPTED`；无日志→`INSUFFICIENT_EVIDENCE`） |
| 真实模型 / 24h 实跑 | **未做** | 本单授权范围外 |

## 4. 命令 / 退出码 / 计数（实测）

最终回归（`w05_final_regression.txt`，`TMPDIR=/tmp/nyaw05.wUlUnu`，Python 3.12.3）：

```
EXIT=0 | journey28            | Ran 28 tests OK
EXIT=0 | move_tool16          | Ran 16 tests OK
EXIT=0 | navigation14         | Ran 14 tests OK
EXIT=0 | runner26             | Ran 26 tests OK
EXIT=0 | pause_race3          | Ran 3 tests OK
EXIT=0 | w05_one_day17        | Ran 17 tests OK
EXIT=0 | worldgen24           | Ran 24 tests OK
EXIT=0 | world_service259     | Ran 259 tests OK
EXIT=0 | whole_repo           | Ran 178 tests OK   (W04 基线 161 + 本单 17)
EXIT=0 | supply_owner_v2      | Ran 122 tests OK
```

- `whole_repo` 由 161 → **178**：**唯一原因是新增 `tests/test_one_day_life.py` 的 17 项**，
  不是 setUp/subTest 展开（W04 回执 158→161 的解释同样如此，历史回执未改）。
- Life 侧既有测试：`components/life/tests/v02/*.py` 是**外部实例 harness**（需要活着的 Supply owner +
  World 服务 + 真模型），本单不可离线跑；`unittest discover -s components/life` = **0 项**（已记录）。
  本单用新 suite 顶替：它在本进程内建真实 `ProductionLifeRuntime` + 真实 Supply owner + 真实 World home。
- Supply 侧：`components/supply/lifesupply/tests/` 无 `__init__.py`，只能
  `discover -s lifesupply/tests -t lifesupply/tests`（122 项 OK，见 `w05_final_regression.txt`）。
- CLI 演练：`w05_cli_rehearsal.json`（`prepare→preflight→run --once→status→pause→run→resume→run→report`，
  全 0 退出，31.2s，1 次替身模型请求）。

## 5. 负对照

`w05_neg_control.txt` / `w05_neg_run.txt`：把 `action_reality_ledger.py` **还原成 W04（HEAD）字节**、
其余（新模块 + 新测试）保持新树 → `test_world_choice_departs_without_arriving_and_is_settled_by_real_time`
**FAIL**：`AssertionError: 'ACTED' != 'IN_TRANSIT'`（`Ran 1 test` / `FAILED`）。
即：旧字节下这一轮会把"出发"当成"到达"上报 —— 行为差异是真的，不是缺模块/导入错误冒充。

## 6. 替身边界（必须照写）

- 决策与内容生成用**确定性替身**（`StubCognition` / `StubWriter`），CLI 演练用 loopback OpenAI 兼容假服务；
  **一次真实模型都没有调用**。
- W05 测试**不启 World gateway socket**：观察缝的替身直接读**同一份** `world_state.json`
  （gateway 也读它），位置权威始终是真实 owner，被替换的只是 socket 传输。
- 世界变更闸门 `WORLD_BODY_INTENT_MUTATION_GATE=canonical` 是 World bridge 自己的**进程环境**开关：
  关闭时 action space 里没有 MOVE 候选。部署时由 supervisor 设（见 runbook），测试里为测试进程打开。
- 独立、短路径、原生 Linux 临时根：`/tmp/nyaw05.wUlUnu`（`mktemp -d` 创建，创建时为空；
  不清理、不动用户文件）。原因：DrvFs 上 AF_UNIX 路径过长与无 POSIX 权限位。

## 7. 偏差 / 未完成 / 阻塞

1. **CLI 级 `run --once` 的轮结果是 `BLOCKED`、作品版本未推进**（cycle 级同一分支是通的：
   真实 Supply owner 版本号 +1）。入口、开关、模型、owner 都对（preflight 全 READY、1 次认知调用），
   该轮 step 为何被拒**未定位到根因**——本轮预算内没有继续深挖。列为未完成项。
2. 世界移动分支只在 cycle 级（进程内）验证；CLI 级没有跑通 MOVE（同上第 1 点 + 闸门来自进程环境）。
3. `intended_target` 旧原型、`ACTION_*` 之外的 AR-0 语义、`settle_world_journey` 的"多段续行"
   只覆盖到单段旅程（夹具世界是 home→邻点一条边）。
4. 真实模型、真实 24h、部署、systemd 自启：**一律未做**（未授权）。
5. Life 侧既有 v02 harness 未跑（需要活实例），只记录命令与 0 项结果。