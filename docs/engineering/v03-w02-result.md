# W02 回执：持久旅程、幂等推进与重启恢复

- 施工单：`D:\NYAIRO-v0.3-W02-持久旅程任务单.md`
- 执行模型：国际版 WorkBuddy DeepSeek `custom/global:deepseek-v4.1-flash`（无子代理委派）
- 交付树：`D:\nyairo-v03-backend`；分支 `work/v03-w01-navigation`；HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`
- 状态：已实现 + 已离线测试；**未 commit、未 push、未部署、未启用任何服务、未调用真实模型**
- 验收环境：WSL Ubuntu + Python 3.12.3（POSIX `fcntl`、tzdata）；`PYTHONDONTWRITEBYTECODE=1`

## 1. W01 未提交前置与归因基线

收到本单时（W02 开工前）已把 dirty 树快照写入 scratch：
`<scratch>\w02_start_status.txt`、`<scratch>\w02_start_sha256.txt`。

W01 前置文件 SHA256（W02 开工前的真实前置）：

| 文件 | W01 前置 SHA256 | W02 后 SHA256 | 是否被 W02 改动 |
| --- | --- | --- | --- |
| `chiyo_bundle/world_move_tool.py` | `b927e89aba3a4d0f…` | `5254e3ac5d309a6f…` | 是（W02 接线） |
| `components/world/scripts/world_runtime_data.py` | `5d3dfee9bc0194f5…` | `5d3dfee9bc0194f5…` | 否（逐字节相同） |
| `components/world/scripts/world_living_skeleton.py` | `3c39cca5dafd1ea3…` | `3c39cca5dafd1ea3…` | 否（逐字节相同） |
| `tests/test_world_move_tool_navigation.py` | `0da2d6ff5f92d5b0…` | `0da2d6ff5f92d5b0…` | 否（逐字节相同） |
| `docs/engineering/v03-w01-result.md` | `6153d03e4f92326b…` | `6153d03e4f92326b…` | 否（W01 回执原样保留，未改成新证据） |

`world_travel_delivery.py`、`scripts/life_heartbeat.py`、`tests/test_world_move_tool.py` 在 W01 未被改动，
因此这三者相对 HEAD 的 diff 就是纯 W02 增量。`world_move_tool.py` 的 diff 混合了 W01+W02，
其 W02 部分在 §3 逐条列出。

最终快照：`<scratch>\w02_final_sha256.txt`、`<scratch>\w02_final_status.txt`。

## 2. W02 增量列表

```
新增  chiyo_bundle/world_journey.py                     1444 行（旅程引擎）
新增  tests/test_world_journey.py                       1020 行 / 28 项测试
+7/-3   components/world/scripts/world_travel_delivery.py   （XINXI_DIR 走请求环境）
+86/-102 scripts/life_heartbeat.py                          （常驻循环 → 一次性显式入口）
+10/-2  tests/test_world_move_tool.py                       （2 条旧断言按新语义更新并加强）
+223/-37 chiyo_bundle/world_move_tool.py                    （W01+W02 混合；W02 部分见 §3）
```

### 2.1 新增 `chiyo_bundle/world_journey.py`

- 单一原子文件账本 `<world_home>/data/world/world_journey.json`（`mkstemp`+`fsync`+`os.replace`+`chmod 0600`），
  与仓库既有持久机制（`world_foundation` / `world_travel_delivery` / `world_timeline`）同构；
  未引入 SQLite/新框架（施工单允许二选一，这里选了与仓库一致的原子文件+内部账本/历史）。
- 严格 schema 校验 + 状态机不变量校验（每次写入前校验，损坏即 fail closed）。
- 公开 API：`accept_journey` / `advance_journey` / `observe_journey` / `load_state` /
  `state_path` / `lock_path` / `graph_fingerprint` / `legacy_intent_present`。
- 图指纹：`sha256(world_id, route_revision, 每个节点的排序 (邻居:耗时) 列表)`。
- 终态历史：`history[]` 保存被替换的终态旅程摘要（最多 16 条），历史不靠删除表示。

### 2.2 `components/world/scripts/world_travel_delivery.py`（+7/−3）

`_current_activity_status(hermes_home, environment=None)`：`XINXI_DIR` 改为读本次请求环境，
`plan_travel` 显式传入 `environment`。此前固定读进程 `os.environ`，另一个实例的
`current_activity.json` 会把本实例的计时规划误判成 `ACTIVE_ACTIVITY_CONFLICT`。
未触碰 gateway/resolver 等无关调用点。

### 2.3 `scripts/life_heartbeat.py`（+86/−102，旧原型退役）

- 删除硬编码 `/srv/nyairo-v0.2.0-rc2/**` 路径、`intended_target.json` 读写、夜间自动回家目标、
  `run_loop()` 常驻循环与 `time.sleep(15)`。
- 改为一次性入口：`main(argv)` 要求显式 `--world-home`（必须绝对路径）、可选 `--world-package`、
  可重复 `--env KEY=VALUE`、可选 `--now`；环境由参数构造（不继承进程世界配置）；
  只调用 `advance_journey`（没有旅程时不创建任何东西）。
- 参数缺失/非法 → 退出码 2 且**不产生任何移动**；兼容旧调用形式 `once`（语义等同一次性）。
- 未实现守护进程/systemd/定时器/宵禁。

### 2.4 `tests/test_world_move_tool.py`（+10/−2，逐条解释）

两条断言按新语义更新（唯一被修改的旧断言，均保留并**加强**了原保障）：

1. `test_real_walking_time_and_in_progress_blocks_redirect`：结算**最后一段**的那次调用现在如实返回
   `arrived`（W02 在旅程引擎内结算并确认到达），不再返回 `already_there`；紧随其后追加一次调用断言
   `already_there`，原"到达即已在该地点"的保障不减反增。
2. `test_timed_transfer_never_teleports_or_skips_a_leg`：同上；并追加一次调用断言 `already_there`。
   其余断言（每段 destination/estimated_minutes/位置不瞬移）全部原样保留。

未删除任何测试、未跳过任何测试、未削弱在途/拒绝保障。

## 3. `world_move_tool.py` 的 W02 增量（W01 部分见 W01 回执）

1. `_label(location_id, world=None)`：**自定义世界用真实名称优先**，只有默认汐见市（`is_custom=False`）
   保留旧短名兼容；`world` 为空时回落到旧短名/节点 ID。
2. 删除 `_remember_intent` / `_clear_intent` 与全部调用点：工具**不再写 `intended_target.json`**。
3. 新增 `_journey_module()`（懒加载 `chiyo_bundle.world_journey`，不在插件启动时导入世界脚本）。
4. 新增 `_journey_result(projection, world, requested)`：把旅程投影映射为既有 JSON 结构
   （保留 `ok/status/message`、`destination_location_id`、`requested_location_id`、`estimated_minutes`、
   `route`、`location_id`、`remaining_minutes`、`reason_code`），新增 `journey_id`/`target_location_id`/
   `segments_confirmed`；`pending` 文案为"旅程已保存，后续推进可继续"（不承诺未启动的服务）。
5. `move_handler` 计时分支改为经旅程引擎：`accept_journey`（首段）/`advance_journey`（续行）；
   已有未确认旅程时：同目标 → 推进；不同目标 → `JOURNEY_TARGET_LOCKED`（`ok=False`、`status=in_progress`、
   返回活动目标与当前段）；关闭计时开关 → `JOURNEY_ACTIVE_TRAVEL_DISABLED`（拒绝瞬移）；
   旅程阻塞 → `JOURNEY_BLOCKED`；旅程账本损坏 → `JOURNEY_STATE_CORRUPT`。
6. 即时模式（关闭计时）保持 W01 逐段经 owner 的既有语义；但**只要存在未确认的计时旅程就拒绝移动**，
   所以"开关在途中变化"不会绕过旅程瞬移。
7. 保留 `realize_due_travel` 前置结算步骤（owner 操作）与既有失败语义
   （坏 travel 状态仍返回 `ARRIVAL_REALIZATION_FAILED`）。

## 4. 状态机

旅程级（`world_journey.json.active.status`）：

```
pending ──(提交当前段成功)──▶ in_transit ──(到期结算+证据一致)──▶ pending(下一段) ──▶ … ──▶ completed
   ▲                              │
   └────(证据 now 未到期/已认领)───┘
证据缺失或矛盾 ──▶ unknown（每次推进重新查证，不命令，不丢目标）
明确拒绝/冲突/损坏 ──▶ blocked（停止状态：不再启动新段，不自动迁移/重规划）
```

段级（`segments[i].status`）：`pending → started → confirmed`；
不变量：`segments[:current_segment]` 全为 `confirmed`，`current_segment` 指向的段非 `confirmed`；
`completed` 时 `current_segment == len(route)` 且全部 `confirmed`。

- 终态（`completed`/`blocked`）在新旅程被接受时归档进 `history`（保留目标与原因），**不删除**。
- `blocked` 之后：`advance_journey` 直接返回该终态、不命令 owner；新的**显式用户请求**可归档旧旅程后
  开新旅程（旧的阻塞记录仍在 history）。`unknown` 不是终态，`advance` 会重新查证。
- 关闭计时开关时不启动新段，且**不**把旅程写成终态（可恢复），仅返回 `TRAVEL_TIME_DISABLED` 拒绝。

## 5. API 与锁顺序

```python
world_journey.accept_journey(*, target, hermes_home, environment, now=None, request_key=None)
    # 仅接受规范节点 ID（名称解析属工具层）；先耐久写意图，再调单段 owner
world_journey.advance_journey(*, hermes_home, environment, now=None)
    # 显式推进：至多启动一个新段；只续行已有用户目标
world_journey.observe_journey(*, hermes_home, environment, now=None)
    # 真正只读：不 realize、不 read_move_state、不创建目录/文件、不改任何字节
world_journey.graph_fingerprint(world) -> "sha256:…"
world_journey.legacy_intent_present(hermes_home) -> bool     # 只读探测，不消费
```

锁顺序（全局唯一顺序，禁止反向）：

```
world_journey.json.lock  →  world_state.json.move.lock  →  world_travel_delivery.json.lock
        （旅程，最外层）            （owner 世界锁）              （owner 旅行锁）
```

- 旅程锁覆盖"读旅程—协调证据—提交单段—更新旅程"，因此跨进程串行（`flock`，非线程锁）。
- owner 内部固定「world → travel」：`apply_move_to`→`start_travel`，`realize_due_travel` 显式 world→travel。
- 本模块**从不**在持有 owner 锁时取旅程锁；也不在持有 owner 锁时递归调用会申请同锁的函数。
- 未使用数据库写事务；旅程锁是纯文件锁，owner 永不申请它，不存在反向依赖，也不会互相等待。

## 6. 关键故障窗口与证据重协调

| 窗口 | 崩溃点 | 磁盘证据 | 恢复动作 |
| --- | --- | --- | --- |
| ① | 意图落盘后 / owner 提交前 | 段 `pending`、无 travel 记录、位置=段起点 | 直接提交该段（一次） |
| ② | owner 提交后 / 旅程落盘前 | travel 记录存在（IN_PROGRESS），旅程仍 `pending` | 按 `execution_id` 认领，**不重复提交**，补写 `started` |
| ③ | 到达结算后 / 段确认前 | travel `ARRIVED` 且位置=段终点，段仍 `started` | 只补写 `confirmed`，不再写世界状态 |
| ④ | 段确认后 / 下一段开始前 | 段 `confirmed`、下一段 `pending`、位置=下一段起点 | 只提交下一段 |

- 确认条件 = 持久 travel 记录（execution_id/travel_id/起终点/状态）**且**世界位置与段终点一致；
  仅凭"现在恰好在目的地"不确认（`EVIDENCE_CONFLICT` 与 `WORLD_LOCATION_CHANGED` 都是反例测试）。
- owner 侧 exactly-once 的真实能力：`start_travel` 对同一 `execution_id` 幂等（重复调用返回
  `already_started`，不再写记录；同 ID 不同路线返回 `EXECUTION_ID_ROUTE_CONFLICT`），
  并对"已有其他在途 travel"直接拒绝；`realize_due_travel` 的到达落地是幂等的
  （位置已在终点时只把记录标记 ARRIVED，不重复移动）。因此引擎的保证是：
  **每个段 execution_id 至多一条 travel 记录、每次到达至多一次世界位置写入**，
  由"先查证再补写"实现，而不是靠假设 owner 无条件 exactly-once。

## 7. 实际执行命令 / 退出码 / 计数

工作目录 `/mnt/d/nyairo-v03-backend`，`PYTHONDONTWRITEBYTECODE=1`，`PI_SCRATCH_DIR=<scratch>`。

| # | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| 1 | `python3 -m unittest discover -s tests -p test_world_journey.py` | **28 tests / OK** | 0 |
| 2 | `python3 -m unittest discover -s tests -p test_world_move_tool.py` | **16 tests / OK** | 0 |
| 3 | `python3 -m unittest discover -s tests -p test_world_move_tool_navigation.py` | **14 tests / OK** | 0 |
| 4 | `python3 -m unittest components.worldgen.tests.test_v04_navigation test_edited_world_runtime test_runtime_wiring` | **24 tests / OK** | 0 |
| 5 | `cd components/world/service && python3 -m unittest discover -s . -p 'test_*.py'` | **259 tests / OK** | 0 |
| 6 | `python3 -m unittest discover -s tests -p 'test_*.py'`（整仓 tests/） | 132 tests / FAILED(errors=9) | 1 |
| 7 | `git diff --check` | 无输出 | 0 |
| 8 | 负对照副本 A（HEAD 接线）跑 #1 | 28 tests / FAILED(failures=4, errors=4) | 1 |
| 9 | 负对照副本 B（回退 2 处 W01 语义）跑 2 个定向测试 | 2 tests / FAILED(failures=2) | 1 |

第 6 项的 9 个 error **全部**是 `test_update.UpdateTransactions.*`，原因仍是本机未执行
`bash scripts/install.sh` 安装依赖（与 W01 相同的既有环境前提，未为本单修复）；本单未跑安装脚本。

## 8. 负对照证据

两份对照都放 scratch，保留同依赖（`components`/`tests` 完整），**没有**回退候选树；
并特意**保留**新模块 `chiyo_bundle/world_journey.py`，使失败是语义失败而非"缺新模块 ImportError"。

### 副本 A：`<scratch>\w02-neg-head`（W02 之前的三处接线）

把 `chiyo_bundle/world_move_tool.py`、`components/world/scripts/world_travel_delivery.py`、
`scripts/life_heartbeat.py` 用 `git show HEAD:` 还原为 W02 之前的实现（其余文件与候选树一致，
新模块保留）。跑 W02 测试：

```
FAILED (failures=4, errors=4)   # 28 项中 8 项失败
FAIL  test_tool_creates_journey_and_reports_in_progress      （旧工具不建旅程、无 journey_id，仍写旧意图文件）
FAIL  test_tool_flag_off_never_teleports_active_journey      （无旅程概念，开关变化语义不同）
FAIL  test_legacy_intent_file_is_inert_and_byte_identical    （旧工具重写 intended_target.json）
FAIL  test_request_environment_wins_for_activity_and_journey （进程 XINXI_DIR 串扰 → ACTIVE_ACTIVITY_CONFLICT）
ERROR test_label_prefers_real_name_in_custom_world           （旧 _label 无 world 参数）
ERROR test_shiomi_builtin_world_journey_completes_with_aliases（旧工具无 _resolve_target）
ERROR test_requires_explicit_world_home_and_never_moves_on_bad_args（旧入口无常驻循环外的显式 API）
ERROR test_one_shot_advance_only_continues_existing_journey  （同上）
```

引擎级用例（accept/advance/observe、四个故障窗口、并发、幂等、证据冲突）在副本 A 中**仍然通过**——
因为引擎模块本身是新代码且被特意保留；这一点如实记录，不把引擎自身语义伪装成"被旧实现证伪"。

### 副本 B：`<scratch>\w02-neg-w01fix`（精确回退 W02 修正的两处 W01 语义）

只把 `_label` 回退成 W01 的"旧短名优先"、把 `_current_activity_status` 回退成读进程 `os.environ`，
其余与候选树一致。定向测试：

```
FAIL  test_label_prefers_real_name_in_custom_world
      AssertionError: '家' != '海边的家'
FAIL  test_request_environment_wins_for_activity_and_journey
      AssertionError: ok False / reason_code='ACTIVE_ACTIVITY_CONFLICT'（进程环境里另一实例的活动文件）
Ran 2 tests / FAILED (failures=2)
```

两处修正的测试在新实现下均通过（§7 第 1 项）。

## 9. 新增测试覆盖（`tests/test_world_journey.py`，28 项）

| 场景（施工单 §7） | 测试 |
| --- | --- |
| A 三段计时旅程 / 每段恰好一次 | `test_three_segment_journey_advances_to_target_once_per_segment`（3 条 travel、revision+3、完成后重复推进零写入） |
| A 室内节点 | `test_journey_targets_indoor_area_node`（`place/area`，逐段按真实耗时推进到最终节点） |
| A 汐见市兼容 | `test_shiomi_builtin_world_journey_completes_with_aliases`（别名经工具层解析，2 段完成） |
| B 耐久写失败 | `test_durable_write_failure_never_calls_owner`（注入原子写失败：owner 未被调用）、`test_unreadable_journey_store_never_calls_owner` |
| C 窗口① | `test_window_intent_written_owner_not_called`（真实子进程 `os._exit(9)`，无 owner 记录） |
| C 窗口② | `test_window_owner_committed_journey_not_updated`（子进程在 owner 提交后终止；恢复不重复提交） |
| C 窗口③ | `test_window_arrival_realized_segment_not_confirmed`（到达已落地未确认；确认不再写世界状态） |
| C 窗口④ | `test_window_segment_confirmed_next_not_started`（段已确认未开始下一段；只提交下一段） |
| D 幂等键 | `test_replay_and_idempotency_key_conflicts`（同键同输入重放、同键异输入冲突、无键同目标重放、异目标拒绝、重复推进不重复提交） |
| D 多进程并发 | `test_concurrent_advance_starts_exactly_one_segment`（`multiprocessing` 屏障同步两个进程，恰好一条新记录） |
| E 第二段拒绝 | `test_second_segment_rejection_blocks_and_keeps_target`（目标不丢、不启动后段、停止状态不再命令） |
| E 证据未知 | `test_external_position_change_holds_target_without_command`、`test_arrived_record_with_moved_position_is_evidence_conflict` |
| F 图变化 | `test_graph_change_blocks_before_any_command`（改一条边耗时 → `GRAPH_CHANGED`，owner 未被命令） |
| F world_id 不同 | `test_world_id_mismatch_blocks` |
| F 外部在途 | `test_external_travel_in_progress_blocks` |
| F 损坏账本 | `test_corrupt_journey_store_fails_closed`、`test_corrupt_travel_store_blocks_without_moving` |
| G 纯 observe | `test_observe_is_read_only_with_and_without_state`（到期前后反复读取：字节+mtime 全不变、位置不变、travel 仍 IN_PROGRESS、world revision 仍为 1；无旅程时不建库） |
| H 开关变化 | `test_tool_flag_off_never_teleports_active_journey`（工具与引擎都拒绝，旅程不被写成终态，恢复后可续行） |
| H 环境冲突 | `test_request_environment_wins_for_activity_and_journey`（坏进程环境 + 另一实例活动状态） |
| H 自定义世界真名 | `test_label_prefers_real_name_in_custom_world` |
| H 旧意图退役 | `test_legacy_intent_file_is_inert_and_byte_identical`（字节不变、不驱动移动、只读报告 `legacy_intent_present`） |
| H 禁用 feature | `test_feature_gate_disabled_starts_nothing` |
| 旧心跳入口 | `test_requires_explicit_world_home_and_never_moves_on_bad_args`、`test_one_shot_advance_only_continues_existing_journey` |

## 10. 已知边界与未闭合项

1. **未提交、未推送、未部署**；分支上保持 6 改 + 4 新增（含 W01 的 3 改 + 2 新增）的未 staged 状态。
2. 计时旅程只有**一个活动旅程**（每 world_home）；W02 不做多旅程队列。
3. `blocked` 是终态：`advance` 不再启动新段；只有新的显式用户请求才会归档旧旅程并开新旅程。
   **没有**提供 cancel/pause API（施工单 §4.12 要求不提供未实现语义的按钮）；如何清理/恢复阻塞旅程
   属 W03 的暂停语义设计。
4. `unknown` 会在每次 `advance` 重新查证；只有证据重新自洽（位置回到段起点、或记录 ARRIVED 且位置一致）
   才会继续，不做自动重规划/迁移。
5. 未实现常驻进程、systemd、自动启动、后台定时器、宵禁或自主目标生成；`life_heartbeat.py` 只做一次性推进。
6. 世界包/图变化只做 fail closed（`GRAPH_CHANGED`），不做自动重规划；位置被外部改动同样 fail closed。
7. `world_body_gateway_api.py` / `world_action_resolver.py` 仍以零参调用 `location_catalog()`（进程环境），
   属 W03 的统一装配范围，本单未改（施工单只要求计时规划链路显式使用请求环境）。
8. 整仓 `tests/` 仍有 9 个 `test_update` 安装前提 error（未跑 `scripts/install.sh`，不属本单范围）。
9. 未做真实模型调用、未动真实 profile/账号/密钥/供应商；未做长测或线上验证。
10. 原生 Windows 仍不可跑该链路（缺 `fcntl`/tzdata）；W02 测试带 `os.name == "nt"` 跳过标记。

## 11. W03 所需接口

- 推进：`world_journey.advance_journey(hermes_home=…, environment=…, now=…)`（显式、有副作用、
  至多启动一段）；纯读 `observe_journey` 可用于状态展示（不结算到达）。
- 只读状态字段：`status / journey_id / target_location_id / current_segment / destination_location_id /
  segment_status / segments_confirmed / remaining_seconds / remaining_minutes / location_id / revision /
  graph_matches / legacy_intent_present / history_count / error`。
- 生命周期：`history[]` 提供终态旅程摘要；`blocked`/`unknown` 需要 W03 定义暂停/放弃/重试语义
  （当前仅"新请求归档旧旅程"这一条出路）。
- 锁契约：新增写者必须遵守 `journey.lock → world.move.lock → travel.lock`；不得在持有 owner 锁时取旅程锁。
- 装配：W03 按 profile/world_home 装配调度入口（不得再引入裸文件写者）；`scripts/life_heartbeat.py`
  已是"显式参数 + 一次性推进"的可用入口，可直接作为 W03 调度的最小调用点。
- 世界/环境：一律用 `world_runtime_data.active_world(environment)`；`_current_activity_status` 等
  owner 侧辅助函数必须显式接收环境（本单已修一处）。

## 12. 范围外写入

- 候选树内：仅 §2 列出的 6 个改动文件 + 2 个新增文件（另有 W01 遗留的 2 个新增文件保持原样）。
  未触碰 `vendor/`、`website/`、`config/`、`patches/`、`components/world/service/**` 等。
- 候选树外：只写会话 scratch（起始/最终快照、两份负对照副本、临时驱动脚本、测试临时世界与实例）。
- 未删除任何用户文件；未清理旧数据；`intended_target.json` 在候选树内没有新写者/消费者，
  测试只验证其字节不变与只读探测。本轮清理了 3 个由本会话 `py_compile` 产生的 `__pycache__` 文件。
- 未 `reset`/`checkout` 任何未知改动，未创建远程 PR。
