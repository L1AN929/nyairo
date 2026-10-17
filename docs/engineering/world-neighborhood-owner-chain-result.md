# 邻居拓扑的物理 owner 链（World 层）—— 测试结果

状态：已落地（只新增测试与文档，**未改任何产品文件**）。
范围：`tests/test_world_neighborhood_owner_chain.py`（新增，3 条用例）；
本文件；`.agents/notes/implemented/testing/2026-10-18-neighborhood-owner-chain.md`（新增）。
生产代码零改动：`git status` 里只有这三个新增文件（见 §7）。

## 1 这包证明什么、不证明什么

证明（World owner 层）：真实 `examples/worlds/daily_neighborhood_v1` 世界（真 `init_instance`）+ 真
`WorldBodyService`（真 `gateway.sock`、真授权租约、真 Resolver、真 travel 记录）+ 真
`FormalOwnerMoveSubmitPort`（Life 侧正式授权入口的**只读引用**，不绕过授权、不直接调 Resolver 的 commit）：

1. 一个数据根上经 `world_journey.accept_journey/advance_journey` 串 4 段旅程，走真实拓扑的 **10 条边**；
2. 每段：提交成功＝**出发而不是到达**（位置仍在段起点、owner travel `IN_PROGRESS`）、**到期前位置不变**、
   **到期后是一次真实到达**（盘上位置变成段终点、travel `ARRIVED`）、execution id/travel id/对账结论
   始终归属同一段；
3. 完成的旅程不会重复 spawn（再推进仍 `completed`，提交数不变）；
4. 暂停（World 控制位）中的在途段可以到达，但本旅程的下一段不出发；关掉原 service lease 后，**真新进程
   （新 PID）+ 新 UDS 绝对路径**在同一数据根上重建，原 socket 保留，随后 resume 只发一段合法新段；
5. owner 真的提交了但回执丢失时，同 execution id 只读认领、**提交数恒 1、对应 travel 恰好 1**
   （认领发生在"提交已落账之后的下一次推进"，不假设提交快于客户端的 deadline；见 §5.1）。

不证明（如实标注）：Life 自主/生活集成（另一位 writer 负责）、6 地点内景与消费、全天节奏、真实模型/外网；
也没有真睡 22 分钟的走时（见 §6）。

## 2 命令与真实 returncode

runner 都在 scratch（`$PI_SCRATCH_DIR/neighborhood-owner/`，`PYTHONDONTWRITEBYTECODE=1`，
`/mnt/d/nyairo-v03-backend`，`w04venv/bin/python`）：

| 命令 | 输出 | returncode |
|---|---|---|
| `bash run_suite.sh`（整包一次） | `Ran 3 tests in 60.789s` / `OK`；复跑 `Ran 3 tests in 89.542s` / `OK`（WSL/9p 抖动，不是用例变慢） | `0` / `0` |
| 父级验收命令原样复播（`replay_acceptance.sh`：Life + Stay + 本包 3 条，**同一进程共 6 条**） | `Ran 6 tests in 194.551s` / `OK`，`PY_EXIT 0`；修复前同一条命令是 `Ran 6 tests in 255.130s` / `FAILED (failures=1)` | `0`（修复后）／`1`（修复前） |
| 故障精确 ID `…LostReplyAfterRealCommitTest` | `Ran 1 test in 8.227s` / `OK` | `0` |
| 慢提交压力（scratch `stress_slow_owner.py`，真实 Resolver 提交人为延迟 1.5s，远大于客户端 0.4s 预算） | `OK`，`delay_seconds=1.5 successful=True` | `0` |
| `…NeighborhoodOwnerChainTest` | `Ran 1 test in 25.584s` / `OK` | `0` |
| `…PausedSegmentRestartTest` | `Ran 1 test in 11.419s` / `OK` | `0` |

旧 suite 按施工单**不全跑**：只跑本包；`test_world_body_slow_owner_verdict.py` 与 `test_world_journey_ambiguous_submission.py` 作为正负范例被读、被复用，未重跑。

## 3 主链实证（fixture `/tmp/nyaw05r.jdK3q/nb-owner-chain-ce18ff5062`）

4 段旅程 / 10 段 / 10 次提交 / 10 条 travel / 10 个不同 execution id；`life_execution.jsonl` 里
每个 execution 恰好 1 条 `committed` + `reason_code=move_departed`，总 committed 条数 10。
journey id：`journey-206655df…`、`journey-7ece8fe5…`、`journey-38eff4f5…`、`journey-278e716b…`（active，`completed`）。

| # | 段 | execution id | travel id | 提交数 |
|---|---|---|---|---|
| 1 | home→street_front | `world-journey-journey-206655df…-0` | `travel-2e6abb6c…` | 1 |
| 2 | street_front→street_corner | `…206655df…-1` | `travel-91d63dea…` | 1 |
| 3 | street_corner→park | `…206655df…-2` | `travel-081780dc…` | 1 |
| 4 | park→street_corner | `…7ece8fe5…-0` | `travel-0649f352…` | 1 |
| 5 | street_corner→cafe | `…7ece8fe5…-1` | `travel-e4669606…` | 1 |
| 6 | cafe→street_corner | `…38eff4f5…-0` | `travel-a2a3c521…` | 1 |
| 7 | street_corner→street_front | `…38eff4f5…-1` | `travel-9a798959…` | 1 |
| 8 | street_front→reading_point | `…38eff4f5…-2` | `travel-9b9bded0…` | 1 |
| 9 | reading_point→street_front | `…278e716b…-0` | `travel-f00edb9a…` | 1 |
| 10 | street_front→home | `…278e716b…-1` | `travel-f66f3e2f…` | 1 |

"提交数"来自 `run/action_lifecycle.jsonl` 的 `state=SUBMITTING` 计数（每段 1），与
`port.submissions == 10`、`len(travels) == 10` 三者一致；10 条 travel 全部 `ARRIVED`，
世界位置最终回到 `home`。旅程完成后再 `advance_journey` 一次：仍 `completed`，提交数仍 10。

真实开关证据（同一 fixture）：`world-home/run/` 下 `gateway.sock` / `control.sock`（owner 0，0600）、
`action_authorizations.json`、`action_lifecycle.jsonl`、`world_body_authority.lock`；旅行/世界/旅程账本在
`world-home/data/`；`data/life_execution.jsonl` 是 Resolver 自己的执行链（v2，`prev_hash` 串起来）。

## 4 恢复边界一：暂停 + 真重启（fixture `nb-owner-restart-7ac112d785`）

进程证据（`workers.jsonl`，真新 PID，父进程 332）：

| 角色 | PID | gateway socket | run 目录 |
|---|---|---|---|
| worker 1 | **576** | `world-home/run/gateway.sock` | `world-home/run` |
| worker 2 | **609** | `worker2-run/gateway.sock` | `worker2-run` |

- 暂停前：`journey-9f4da9bd…-0`（home→street_front）已发，`port.submissions == 1`，位置 `home`；
- World 控制位暂停（`world_control.set_paused`，持 journey 锁）后推进：仍 `in_transit`（在途段如实报告），
  `submissions` 仍 1，travel 仍 1；
- worker 1 收 SIGTERM：`serve()` 退出、`returncode=0`、`finally` 里只 `lease.release()`；之后
  `world_body_authority.lock_is_held(world-home/run) == False`；`world-home/run/gateway.sock` 的
  `st_dev/st_ino` 前后一致（旧 socket 未被 unlink），worker 2 启动后仍一致；
- worker 2（新 PID 609、新 run 目录）起来后：`world_journey.json` / `world_travel_delivery.json` /
  `world_state.json` **字节未变**；active 仍 `in_transit`、segment 0 的 execution id 与 travel id 不变、
  travel 仍只有 1 条、`committed` 仍 1 条；
- 新 socket 上到期（`due+1s`）推进：`segments_confirmed=1`、位置 `street_front`、`submissions==0`
  （暂停挡住新段）；
- resume 后推进：`TRAVEL_STARTED`、`submissions==1`（新端口）、travel 变 2 条，新段
  `…9f4da9bd…-1` street_front→street_corner（`IN_PROGRESS`），段 0 变 `confirmed` 且 id 未改；
- 全程 `action_lifecycle.jsonl` 每个 execution 只有 1 条 `SUBMITTING`：`{…-0: 1, …-1: 1}`。

产品耦合（不是缺陷，但必须知道）：gateway 用 `service.config.run_dir` 找授权注册表
（`world_body_gateway_api.py:169-174`），签发侧读 `WORLD_ACTION_AUTH_REGISTRY_DIR`
（`intent_executor.py:242-247`）。换了 run 目录就必须把签发侧指向同一个目录，否则回执是
`AUTHORIZATION_NOT_FOUND`（本包第一次跑就是这样红的，见 §8 的真缺陷/耦合清单）。测试里对应的动作是
`ChainHarness.retarget_auth_registry()`。

## 5 恢复边界二：回执丢失、owner 已提交（fixture `nb-owner-lostreply-92daacefce`）

注入点只在 test 的传输层：`GatewaySurface.submit_action` 先走**真实**提交（Resolver + 账本 committed），
再由事件屏障挂住回执；`GatewaySurface.get_action_result` 在同一层注入失败。

用例分三步，全部与"提交快不快"无关（§5.1 就是这条被父级同进程验收打红过一次的更正）：

1. **故障窗口本身**：`accept_journey` 期间回执被挂住 → 端口只报 `uncertain`。那一刻 owner 的 travel
   记录**可能还没出现**（真实提交比客户端 0.4s 预算慢时就是这样），所以旅程的第一次投影允许是
   `unknown`（还没有证据）或 `started`（已经认领），**终态一律不许**；
2. **owner 侧证据**：有界等待"真实提交真的落账"（`commit_done.wait(60)`），再断言提交恰好一次
   （端口 1 次、gateway 1 次、账本 `attempts=1`）、travel 恰好 1 条、位置不动；
3. **只读对账认领**：在"提交已落账之后"再推进一次 → 同 execution 认领为 `started`/`in_transit`，
   `travel_id/started_at/expected_arrival_at` 全部等于 owner 记录，位置仍 `home`，`submissions` 仍 1。

实测（父级验收复播的 fixture `nb-owner-lostreply-92daacefce`，一段 home→street_front）：

| 事实 | 值 |
|---|---|
| 端口归类 | `intent_status=UNKNOWN`（`transport_fault:TIMEOUT`），不是成功也不是拒绝 |
| lifecycle | `CREATED → AUTHORIZED → SUBMITTING → SUBMITTED → UNKNOWN`，`SUBMITTING` 计数 1 |
| 账本 | `prepared` 1 条、`committed` 1 条（`world.travel=travel-37b92fb8…`） |
| 旅程 | 段 `confirmed`，`started_at 09:22:30Z` / `expected_arrival_at 09:24:00Z` 与 owner 记录逐字相同，`status=completed` |
| 提交数 | `port.submissions == 1`、gateway 收到 1 次、travel 恰好 1 条 |
| 到期前两次推进 | 仍 `in_transit`，`submissions` 恒 1，execution id 不变 |
| 到期后 | `segments_confirmed=1`、travel `ARRIVED`、位置 `street_front`，仍然只有 1 条 travel |

没有把旧 `blocked` 账本改成 `unknown`、没有迁移任何旧 fixture；"无证据 → 仍非终态、只对账不重发"
一条沿用 `tests/test_world_journey_ambiguous_submission.py` 已证的行为（本包不重刷）。

### 5.1 父级同进程验收里红过一次：根因、修改与证据

**现象**（父级日志 `parent-neighborhood-acceptance.log`，同一进程 6 条）：前 5 条绿，
`LostReplyAfterRealCommitTest` 在 `assertTrue(self.commit_done.is_set())` 处红，`Ran 6 tests in 255.130s`。

**根因（不是产品故障）**：第一版屏障只等"客户端已放弃"（`client.metrics.submitted >= 1`），
于是**提前放行**；真实 Resolver 的提交在同进程有 5 条用例残留负载时要 ≈0.9s，比客户端 0.4s 预算慢，
所以断言跑在提交落账之前。红的那次 fixture 自己就是证据：
`action_lifecycle.jsonl` 里 `SUBMITTED 09:10:57Z → UNKNOWN 09:10:57Z`，而执行账本
`prepared 09:10:57.366Z → committed 09:10:58.263Z`（**owner 真的提交了，而且只有 1 次**；travel 1 条
`IN_PROGRESS`、位置仍在 `home`）。也就是说：没有提交拒绝、没有异常、没有进程内 mock 泄漏——
只有我这条测试把"提交是否已发生"写成了竞速。同一根因还有第二处：旅程在 `accept` 内只对账一次，
提交慢于客户端时那一刻还没有 travel 记录，旅程合法地停在 `unknown`（非终态），认领必须发生在
**下一次推进**，第一版却断言在 `accept` 的返回里。

**修改**（只改测试，产品零改动）：

1. 屏障改成等**两条事实**：`commit_done`（真实提交已落账）**且** `client.metrics.submitted >= 1`
   （客户端确实等满 deadline 才放弃）。两者都成立才放行 → 回执必然迟到，但放行绝不早于提交。
2. 断言改成有界等待 + 分阶段：`commit_done.wait(60)` 之后才断言 owner 证据；`accept` 的投影只断言
   "非终态（`unknown`/`started`/`in_transit`，禁止 `blocked`/`completed`）"；认领（`started`/
   `in_transit` + owner 三个时间/身份字段逐字相等）由紧接着的一次 `advance_journey` 断言。
3. 顺带强化：新增 `client.metrics.submitted == 1` 与 `client.metrics.timeouts >= 1`——证明故障窗口
   真实存在（客户端确实是自己等满 deadline 才放弃，不是回执被正常送达）。

**证据**：故障精确 ID 单跑 `Ran 1 test in 8.227s` / `OK`；把真实提交人为拖到 1.5s（≫ 客户端 0.4s 预算，
即父级那次的形状，scratch `stress_slow_owner.py`）仍 `OK`；父级验收命令原样复播 6 条
`Ran 6 tests in 194.551s` / `OK`。断言一条没放松，只把"谁先谁后"从竞速改成事实链。

## 6 时钟：实际 scope（不许含糊）

- **正式 submit 接口内部的 owner 回执时钟不可注入**：`FormalOwnerMoveSubmitPort` 把 `now` 交给意图，
  但 `world_action_resolver` 提交时调 `world_living_skeleton.apply_move_to(...)`
  （`world_action_resolver.py:942-947`）**不传 `now`**，travel 的 `started_at/expected_arrival_at`
  永远是 owner 进程的墙钟。给 `WorldBodyService(now=...)` 传冻结时刻只能改服务戳与开门时间规则
  （`world_action_resolver.py:563-567`），改不动回执——实测：注入 `2026-10-17T07:00:00Z` 后，
  travel 仍然写 `2026-10-17T08:32:09Z`（墙钟）。
- 因此本包的时钟纪律是：journey 侧一律显式传 aware `now`，取值**只**由 owner 公布的
  `expected_arrival_at` 推出 —— 到期前用 `due-1s`（**只读**：`read_move_state` / `observe_journey`），
  到期后用 `due+1s` 推进；不真睡 1320 秒走时，也不宣称"owner 与 journey 共用同一个注入时钟"。
- 后果（实测，来自 `nb-owner-chain-ce18ff5062`）：owner 回执 `started_at` 是墙钟（08:45:48…08:46:26），
  而 journey 侧推进到 `due+1s`，尾部时刻最远到 `08:48:52` —— 即 journey 时钟最多领先 owner 回执
  ≈ 一段最长走时（180s 量级），不是累积到 22 分钟。因为无法用同一时钟，段内"到期前"只用**只读**读取表示，
  **绝不**用倒退回过去的时刻写账本（那才会制造真正的两套时序矛盾）。
- 另：世界包不能"缩短走时来换单一时钟"——`walk_seconds` 最小 30（`worldgen/schema.py:277`），
  且 `MANIFEST.json` 校验每个文件 SHA256（`schema.py:749`），改包会被 loader 拒绝；要真统一只能改产品
  （给 resolver 传 `now`），本批无权改。

## 7 门禁 / 未做

- `git status --porcelain` 只含三个新增文件；`git diff --check` 无输出（见下）。
- note 树与格式：`node --experimental-strip-types …/verify-agent-note-tree.ts`、
  `…/verify-agent-note-format.ts` 均 `ok`。
- 夹具：24 个 `/tmp/nyaw05r.jdK3q/nb-owner-*`（合计 2.4 MB）全部保留；没有 rmtree / unlink /
  TemporaryDirectory 清理 / 删 pyc。子进程源码与 `workers.jsonl`（真 PID）落在夹具里。
- 未做：Life 自主与生活集成、6 地点内景/消费、全天节奏、N 轮 soak、真实模型/外网、真实服务部署、
  `commit/push/stage`。

## 8 真实缺陷 / 产品耦合（本批**未修**，交父级裁决）

1. **owner 回执时钟不可注入**（结构性，非缺陷）：`world_action_resolver.py:942-947` 调
   `world_living_skeleton.apply_move_to` 不传 `now`，所以任何"正式提交"的回执都是墙钟。影响：无法在
   不真睡走时的前提下同时拥有"journey 与 owner 同一注入时钟"。本次没有改产品；若父级要真统一，窄修点就是
   给该调用补 `now=self._clock()`（同一个 resolver 已经有 `self._clock`）。
2. **授权注册表绑在 `config.run_dir`**：`world_body_gateway_api.py:169-174` 用服务自己的 `run_dir`
   找注册表，签发侧用 `WORLD_ACTION_AUTH_REGISTRY_DIR`（`intent_executor.py:242-247`）。换 run 目录
   （例如换 UDS 路径做重启）会让两边失配，回执变 `AUTHORIZATION_NOT_FOUND`。这不是缺陷，但意味着
   "跨 run 目录迁移已签发授权"没有产品支持；本批只在测试侧把签发方指向新 run 目录，产品未动。
3. **`service.shutdown()` 与"保留 socket"互斥**：`world_body_service.py:750-765` 的 shutdown 会 unlink
   自己绑过的两个 socket。想让"重启没重置路径"可验证，就只能退出时不调 shutdown（本包做法），
   或者等产品提供一个"只放租约不清路径"的关闭开关。
4. **未发现 owner 链本身的真实缺陷**：10 条边全部 1 次提交 / 1 条 travel / 1 条 committed；
   暂停态下已发段照旧结算、新段被挡；回执丢失只对账一次。这三条都没有出现需要窄修的地方。
