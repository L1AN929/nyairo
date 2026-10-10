# v0.2 E12.3 验收矩阵：T01–T20

分支 `v0.2-autonomous-life`，起点 `f589aa0`。本表只写**实际跑过的**结果。
`PASS` 只在实跑通过时给出；证据不足的一律 `NOT_RUN` 或 `DEFERRED`。

**证据层级**：`TESTED_ISOLATED` = 隔离环境、真实 owner、受控认知替身；`E2E_VERIFIED` = 真实
owner 端到端（含真实 World 进程）。**本批未调用任何付费模型**，因此没有任何一项是"真实模型
自主选择"的证据——替身给出的决定一律注明。

## 统一执行入口

| 脚本 | 覆盖 |
|---|---|
| `components/check_life_v02.py`（`run_isolated_acceptance.sh`） | T02 T03 T08 T09 T10 T11 T15 T19 |
| `components/check_life_memory_v02.py` | T11 T12(DELETE 半边) |
| `components/check_life_b7.py` | T01(机制) T14 T15 T18 T19 |
| `components/check_life_b8.py` | T14（空窗口） |
| `components/life/tests/v02/b8_assembly_accept.py` | T01 T18 T19 |
| `components/life/tests/v02/b8_negative_accept.py` | T09 T10 T19 |
| `components/life/tests/v02/b8_matrix_accept.py`（本批新增） | T04 T05 T06 T07 T13 T17 |
| `components/life/tests/v02/run_world_move_e2e.sh` | T16 |
| `components/life/tests/v02/run_model_e2e.py run`（B4，本批未复跑） | T02/T03 的真实模型版本 |

## 矩阵

| ID | 原始验收条件 | 执行命令 | 环境 / 入口 | 实际状态与 owner 回执 | 替身/真实 | 证据 | 状态 |
|---|---|---|---|---|---|---|---|
| **T01** | 无用户新指令，打开合法自主机会 | `b8_assembly_accept.py`；`check_life_b7 [2][4]` | 隔离 Supply owner + 真实 `ProductionLifeRuntime` | `autonomous_life()` 装配 `READY`（复用持有 lease 的 runtime）；`run-once` 真实进入 `AutonomousLifeCycle.evaluate_once`；无候选时 `IDLE_NO_CANDIDATE` 且 0 次认知调用 | 替身决定 | `B8_ASSEMBLY PASS`、`B7_CHECK PASS` | **PASS** `TESTED_ISOLATED` |
| **T02** | 首次 Artifact 编辑真正产生版本和 Action | `run_isolated_acceptance.sh` | 隔离 Supply owner | `step_path=AR0`、`action_status=SUCCEEDED`、owner 回执 `OK`/`version_created=True`，v1→v2 | 替身决定 / 真实 owner 写入 | `V02_CHECK [3]` | **PASS** `TESTED_ISOLATED` |
| **T03** | 第二轮接续同一作品，旧 head 影响新步骤 | 同上 | 同上 | 同一 Activity、`parent_version_id` = 上一版、checkpoint 从 `chk:stage:001` 前移 | 同上 | `V02_CHECK [4]` | **PASS** `TESTED_ISOLATED` |
| **T04** | 真实 NO_ACTION 无副作用、正常结束 | `b8_matrix_accept.py [T04]` | 真实 runtime + 真实 owner | `outcome=NO_ACTION`、无 `action_id`、版本号不变、Activity 集合不变、无经历 handoff | 替身决定 | 本批新增 | **PASS** `TESTED_ISOLATED` |
| **T05** | DEFER 保留合法状态，不自动强制执行 | `b8_matrix_accept.py [T05]` | 同上 | `outcome=DEFER`，且**不等于** NO_ACTION；无动作、作品未动、作品仍 ACTIVE 可续 | 替身决定 | 本批新增 | **PASS** `TESTED_ISOLATED` |
| **T06** | Activity 暂停/安全中断：checkpoint 保留，聊天正常 | `b8_matrix_accept.py [T06]` | 同上 | 中断后**没有**产生第二个 Activity，原 Activity 与其 checkpoint 仍在，作品可读，聊天投影仍可渲染 | 替身决定 | 本批新增 | **PASS（部分）** — 见下 §缺口 |
| **T07** | 放弃活动：正常退役、作品保留、不自动恢复 | `b8_matrix_accept.py [T07]` | 同上 | 作品保留未删；没有自动恢复成第二个并行 Activity；**放弃动作本身未被触发** | 替身决定 | 本批新增 | **PASS（部分）** — 见下 §缺口 |
| **T08** | Supply NO_CHANGE：无新版本、不虚报进度 | `run_isolated_acceptance.sh` | 真实 owner | `outcome=ALREADY_DONE`、`owner_version_created=False`、版本数不变 | 真实 owner | `V02_CHECK [6]` | **PASS** `TESTED_ISOLATED` |
| **T09** | expected_head stale → CONFLICT，不覆盖他人写入 | `run_isolated_acceptance.sh`；`b8_negative_accept.py [N02]` | 真实 owner + 真 AR-0 CAS | `owner_status=CONFLICT`、AR-0 `FAILED`；head 保持另一写入者的版本，被拒的一笔不在作品里 | 真实 owner | `V02_CHECK [7]`、`B8_NEGATIVE PASS` | **PASS** `TESTED_ISOLATED` |
| **T10** | commit 回执丢失 → UNKNOWN 查原 operation，不重复提交 | `run_isolated_acceptance.sh`；`b8_negative_accept.py [N05]` | 真实 owner（注入丢回执） | `STEP_UNKNOWN`；用**原 execution_id** 查到 SUCCEEDED；`submit_call_count == 1` | 真实 owner | `V02_CHECK [8]`、`B8_NEGATIVE PASS` | **PASS** `TESTED_ISOLATED` |
| **T11** | 作品已提交 + Memory consumer 失败 → 不回滚 | `run_isolated_acceptance.sh [11]` | 真实 owner + 坏 Memory 路径 | 轮次仍 `ACTED`、版本 +1、Activity 仍 ACTIVE、经历 pending、且是 disposition 不是 error | 真实 owner | `V02_CHECK [11]` | **PASS** `TESTED_ISOLATED` |
| **T12** | 纠正错误的作品记忆：旧错误不再影响判断，S01 head 不变 | `check_life_memory_v02.py [5]` | 真实 M0 + MemoryControlLedger | **DELETE / 停止召回**已通过：被删除的经历不再进视图，M0 证据未被改动。**原生生活经历的 CORRECT 未实现** | 真实 owner | `MEMORY_CHECK [5]` | **DEFERRED** — 见下 §缺口 |
| **T13** | 权限撤回：新写请求拒绝，历史作品仍在 | `b8_matrix_accept.py [T13]` | 真实 Supply owner | owner 拒绝未获授权的写入（`UNKNOWN`），版本数不变、被拒文本不在作品里、历史仍可读 | 真实 owner | 本批新增 | **PASS（部分）** — 用的是"owner 不认识的 grant"，不是**在线撤回**一枚已发 grant |
| **T14** | 空闲日：零候选、零认知调用、无编造经历 | `check_life_b7 [2][8]`；`check_life_b8 [5]` | 受控时钟 | 空窗口 `IDLE_NO_CANDIDATE`、`llm_calls=0`、无 step journal、无编造经历、预算不被消耗 | 无模型 | `B7_CHECK`、`B8_CHECK` | **PASS（机制）** — 真实 24h 属 B8-6 |
| **T15** | 预算耗尽：无新副作用，续作条件保留 | `check_life_b7 [7]`；`V02_CHECK [9]` | 隔离 | 认知/轮次预算耗尽 → 唤醒 `BLOCKED`（不是"她选择休息"）；作品与 Activity 不动；重启后消耗仍在 | 替身决定 | `B7_CHECK`、`V02_CHECK [9]` | **PASS** `TESTED_ISOLATED` |
| **T16** | 一个 World 合法动作真正改变 owner 状态 | `run_world_move_e2e.sh` | 真实 World 服务 + 真 gateway/resolver | `home`→`nearby_street`，revision 1→2，重启后保持；不可达 MOVE 被拒且世界不变；UNKNOWN 用原 execution_id 对账、不二次移动 | 真实 World owner | `WORLD_MOVE_ACCEPTANCE PASS` | **PASS** `E2E_VERIFIED` |
| **T17** | 未感知的 World 变化不泄漏成已知候选 | `b8_matrix_accept.py [T17]` | 观测→候选翻译层 + 动作空间 | 只有被感知的目的地成为机会；未感知地点不在其中；动作空间只含世界自己暴露的候选 | 无模型 | 本批新增 | **PASS** `TESTED_ISOLATED` |
| **T18** | 重启后自主恢复，无重复副作用 | `b8_assembly_accept.py [6]`；`b8_negative_accept.py [N05]` | 隔离 | 重启后开关/预算/事件消费保持；已观察 head 不再算新闻；UNKNOWN 按原 id 对账、不复用旧动作 | 替身决定 | `B8_ASSEMBLY`、`B8_NEGATIVE` | **PASS** `TESTED_ISOLATED` |
| **T19** | 关闭自主开关：新动作停止，聊天与数据正常 | `b8_assembly_accept.py [8]`；`b8_negative_accept.py [N05]`；`V02_CHECK [9]` | 隔离 | 关闭后新决策 `DISABLED`；Activity/checkpoint 保留；聊天投影与 status 仍工作 | 替身决定 | 同上 | **PASS** `TESTED_ISOLATED` |
| **T20** | 旧 profile 兼容：原命令、记忆、Supply 数据可用 | — | — | **没有跑**。缺一个 v0.1 形态的兼容 profile fixture；本轮不允许用"删库新建空 profile"代替 | — | — | **NOT_RUN** |

## 缺口（不得当成 PASS）

- **T06 / T07（部分）**：有前台 Activity 时，AG-1 会**按规则先解析**（`CONTINUE` 路径），不会
  把"暂停/放弃"交给模型，因此我用真实入口只证明了 T06 的 checkpoint 保留 + 聊天正常，以及
  T07 的作品保留 + 不自动恢复。**"由一次决策真正触发暂停/放弃并让 Activity 退役"这条路径没有
  被本批覆盖**；要走通它需要经 AG-1 的 `CONTINUE_CURRENT` 合法决策（`PAUSE`/`ABANDON` 只从该候选
  合法）或用 ActivityCommandService 直接驱动。剩余条件：一条 `decision=ABANDON` 且 Activity 转为
  非 ACTIVE 的真实回执。
- **T12 `DEFERRED`**：现有 Memory Control 只支持**同一会话内用户撰写事件**的 CORRECT；
  `memory_control.py:132-133` 要求替换事件来自用户。原生生活经历没有用户事件，伪造一条用户消息
  就会违反 E07，所以本批不实现、不伪造。已通过的是 DELETE/停止召回。缺口：生活经历的 CORRECT
  需要一个新的、受治理的纠正入口（属独立工作包，且必须写入发布限制）。
- **T13（部分）**：证明的是"owner 不认识的 grant 不能写、历史仍在"，**不是**"在线撤回一枚已发
  grant 之后不能写"。后者需要 Supply 的 revoke 流程，本批未接。
- **T14**：只证明零调用**机制**。真实一整天的观察属 B8-6，未完成前不得标 `SOAK_VERIFIED`。
- **T20 `NOT_RUN`**：缺旧 profile fixture。

## 本批发现与修复的源码问题

**无。** 本批只新增验收脚本；所有拒绝都由既有契约产生，没有新增任何防护，也没有修改任何
正确契约去迎合断言。唯一"发现的"行为是 T06/T07 记录的 AG-1 前台 Activity 规则优先——那是
设计如此，不是缺陷。

## 回归

`scripts/test.py` 的 owned 套件：`closeout / native / memory / supply / world(259) / life /
life-v02 / life-memory-v02 / life-b7` → `FAILED_SUITES: none`；`B7_CHECK`、`B8_CHECK`、
`B8_NEGATIVE`、`B8_ASSEMBLY`、`B8_MATRIX` 全 PASS；`V02_CHECK`/`ISOLATED ACCEPTANCE` PASS；
`MEMORY_CHECK` PASS。真实 World：`WORLD_MOVE_ACCEPTANCE PASS`。

## B8-5 开工前的阻塞

1. **T20 需要旧 profile fixture**（一个 v0.1 形态的实例目录），否则兼容性无法验收。
2. **T06/T07 的决策路径**需要一个能产生 `ABANDON`/`PAUSE` 的合法 `CONTINUE_CURRENT` 决策入口。
3. **T12 是产品级缺口**，不是测试缺口：需要决定是否新增受治理的生活经历纠正入口。
4. **常驻驱动仍未定**（B8-2 遗留）：`runner_tick()` 目前只由 CLI/宿主驱动，没有后台线程；
   B8-6 的长测必须先定下常驻方式。
