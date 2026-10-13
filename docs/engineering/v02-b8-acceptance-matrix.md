# v0.2 E12.3 验收矩阵：T01–T20

分支 `v0.2-autonomous-life`。本表只写**实际跑过的**结果。
`PASS` 只在实跑通过时给出；证据不足的一律 `NOT_RUN` 或 `DEFERRED`。

**证据层级**：`TESTED_ISOLATED` = 隔离环境、真实 owner、受控认知替身；`E2E_VERIFIED` = 真实
owner 端到端（含真实 World 进程）**且**决定与内容都来自真实供应商（没有替身）；`SOAK_VERIFIED`
= 跑满 24 小时的完整观察窗；`DEPLOYED` = 真实生产切换。B8-8 起本批**真的调用了付费模型**
（`gemini-3.8-flash-high`，`https://gw.929711.xyz/v1`），凡是由替身给出的决定一律注明。

**B8-8（本批）新增的唯一入口**：`components/life/tests/v02/run_e14_chain.sh`
（`chain` = E14 六阶段单实例长链；`lifecycle` = T06/T07 的暂停/放弃生命周期；
`probe <mode>` / `stage5` / `stage6` = 单阶段复跑）。逐条真实输出见
`docs/engineering/v02-autonomous-life-implementation.md`。

## 统一执行入口

| 脚本 | 覆盖 |
|---|---|
| `components/life/tests/v02/run_e14_chain.sh chain`（本批新增） | T01 T02 T03 T05 T17 T18 T19（真实模型版）+ 24h 观察窗对照 |
| `components/life/tests/v02/run_e14_chain.sh lifecycle`（本批新增） | T06 T07（完整暂停 / 放弃生命周期） |
| `components/check_life_v02.py`（`run_isolated_acceptance.sh`） | T02 T03 T08 T09 T10 T11 T15 T19 |
| `components/check_life_memory_v02.py` | T11 T12(DELETE 半边) |
| `components/check_life_b7.py` | T01(机制) T14 T15 T18 T19 |
| `components/check_life_b8.py` | T14（空窗口） |
| `components/life/tests/v02/b8_assembly_accept.py` | T01 T18 T19 |
| `components/life/tests/v02/b8_negative_accept.py` | T09 T10 T19 |
| `components/life/tests/v02/b8_matrix_accept.py` | T04 T05 T06(旧) T07(旧) T13(旧) T17 |
| `components/life/tests/v02/run_world_move_e2e.sh` / `run_world_instance.sh` | T16（E14 S5 复用了同一条授权与执行链） |
| `components/life/tests/v02/run_t20_legacy.sh` / `t20_verify.sh` | T20（B8-7） |
| `components/life/tests/v02/run_resident_soak.sh` + `run_resident.sh` | T14 的真实 24h 观察窗 |

> **执行限制（RC1-03）**：`components/life/tests/v02/run_isolated_acceptance.sh` 里有
> `pkill -f supply_service.py` 与 `rm -rf /srv/nya02`；`run_world_instance.sh` / `run_world_move_e2e.sh`
> 里也有 `pkill`。在 `/srv/nya02/resident2` 的长测正在跑的时候**不得**直接执行它们；要跑就先把脚本复制
> 到隔离环境、把共享路径换掉。RC1-03 因此**没有**重跑 T16（World MOVE）与 isolated acceptance，只做
> 只读复核（`check_life_v02.py` / `check_life_b7.py` / `check_life_b8.py` / `check_life_b8_6a.py`
> 本身不引用 `/srv/nya02`，可以安全单独运行）。

## 矩阵

| ID | 原始验收条件 | 执行命令 | 环境 / 入口 | 实际状态与 owner 回执 | 替身/真实 | 证据 | 状态 |
|---|---|---|---|---|---|---|---|
| **T01** | 无用户新指令，打开合法自主机会 | `run_e14_chain.sh chain`（S1+S2）；`b8_assembly_accept.py`；`check_life_b7 [2][4]` | 隔离 profile `chiyo-e14`（真实 Supply owner + 真实 World owner + 真实模型） | 常驻进程启动后**没有任何用户消息**：开关打开前 `DISABLED`（0 次调用），07:46:34 的检查窗真实进入 `AutonomousLifeCycle.evaluate_once`，真实模型给出 `decision=START`，owner 提交 V2；`event_wakes=0`（整轮没有任何事件/用户输入触发） | **真实模型决定** | `E14 S1/S2`（Decision `dec:7c7a1922cc318753`） | **PASS** `E2E_VERIFIED` |
| **T02** | 首次 Artifact 编辑真正产生版本和 Action | `run_e14_chain.sh chain`（S2）；`run_isolated_acceptance.sh` | 隔离 profile（真实 Supply owner） | `step_path=AR0`、Action `actn:6696072f3fbf8df8` / `FILE_ACTION` / `SUCCEEDED`，owner 回执 `owner committed artifact-version:b641fc0486444fb69cb4949d8fa3b814`；作品 `artifact:db42c367d9c049dcbbfc7ecf2e5b3edc` v1→**V2**（parent 是 v1）；M0 真实入账一条 `LIFE_VERIFIED_OUTCOME` | **真实模型决定 + 真实模型内容** | `E14 S2`；`V02_CHECK [3]` | **PASS** `E2E_VERIFIED` |
| **T03** | 第二轮接续同一作品，旧 head 影响新步骤 | `run_e14_chain.sh chain`（S3）；`run_isolated_acceptance.sh` | 同上 | 进程正常停止后**重启同一 profile**：同一个 `artifact_id`，AR-0 的 `expected_head` 正是 V2，产出**V3**（`artifact-version:d2a38b43064b4732b659aa6c6de70a9e`，parent=V2），Action `actn:f0dabc061ffd3d35` 与第一轮不同、operation 也不同（`life:52a687e2f0a04d2c48b972cc950f57c9` vs `life:9ab58d30b2f34b4993f907e92c51dad3`）；工作区里只有这一个作品，没有同名并行作品 | **真实模型决定 + 真实模型内容** | `E14 S3` | **PASS** `E2E_VERIFIED` |
| **T04** | 真实 NO_ACTION 无副作用、正常结束 | `b8_matrix_accept.py [T04]` | 真实 runtime + 真实 owner | `outcome=NO_ACTION`、无 `action_id`、版本号不变、Activity 集合不变、无经历 handoff | 替身决定 | `b8_matrix_accept.py` | **PASS** `TESTED_ISOLATED` |
| **T05** | DEFER 保留合法状态，不自动强制执行 | `b8_matrix_accept.py [T05]`；`run_e14_chain.sh chain`（S4） | 同上 + 隔离 profile 真实模型 | 替身版：`outcome=DEFERRED`、**不等于** NO_ACTION、无动作、作品仍 ACTIVE 可续。E14 S4 的真实模型**真的说了 `DEFER`**（两次：07:48:39、07:49:39，各 1 次调用，`action_id` 为空），此后版本链仍然只有 3 个版本；但该窗口按 `--no-act` 启动，"不执行"这一半由关闭执行保证，故只作真实决定的补充证据 | 替身决定（主证据）/ 真实模型（补充） | `b8_matrix_accept.py`；`E14 S4` | **PASS** `TESTED_ISOLATED` |
| **T06** | Activity 暂停/安全中断：checkpoint 保留，聊天正常 | `run_e14_chain.sh lifecycle` | 隔离 profile `chiyo-e14-matrix`（真实 Supply owner + 真实 LR-2/LR-5） | **完整暂停生命周期**：ACTIVE → 合法 `PAUSE` 决定 → AG-2 采纳 → LR-2 提交 `trn_54a94702ae0c45f8`（`from_status=ACTIVE` → `to_status=PAUSED`，`decision_ref=dec:f8c9ae7d09b362d6`、`adoption_ref=adp:004c0219e9f4eb7c`、`result_revision=2`、`reason_code=VOLUNTARY_PAUSE`）；checkpoint 前后一致、作品 head 未动、`LifeProjection` 仍可渲染 | 受控决定词（**不是**真实模型选择） | `E14_LIFECYCLE: PASS` | **PASS** `TESTED_ISOLATED` |
| **T07** | 放弃活动：正常退役、作品保留、不自动恢复 | `run_e14_chain.sh lifecycle` | 同上 | **完整放弃生命周期**：ACTIVE → 合法 `ABANDON` 决定 → AG-2 采纳 → LR-2 提交 `trn_62156b5dc1524b7e`（`ACTIVE`→`ABANDONED`，`dec:e2ced3d28a567a15`、`adp:e5aabe49d5ab28ff`、`result_revision=2`、`ABANDONED_BY_DECISION`）；前台引用清空、`list_open_activities()` 为空、作品内容仍可读、下一次机会没有自动恢复成第二个 Activity | 受控决定词（**不是**真实模型选择） | `E14_LIFECYCLE: PASS` | **PASS** `TESTED_ISOLATED` |
| **T08** | Supply NO_CHANGE：无新版本、不虚报进度 | `run_isolated_acceptance.sh` | 真实 owner | `outcome=ALREADY_DONE`、`owner_version_created=False`、版本数不变 | 真实 owner | `V02_CHECK [6]` | **PASS** `TESTED_ISOLATED` |
| **T09** | expected_head stale → CONFLICT，不覆盖他人写入 | `run_isolated_acceptance.sh`；`b8_negative_accept.py [N02]` | 真实 owner + 真 AR-0 CAS | `owner_status=CONFLICT`、AR-0 `FAILED`；head 保持另一写入者的版本，被拒的一笔不在作品里 | 真实 owner | `V02_CHECK [7]`、`B8_NEGATIVE PASS` | **PASS** `TESTED_ISOLATED` |
| **T10** | commit 回执丢失 → UNKNOWN 查原 operation，不重复提交 | `run_isolated_acceptance.sh`；`b8_negative_accept.py [N05]` | 真实 owner（注入丢回执） | `STEP_UNKNOWN`；用**原 execution_id** 查到 SUCCEEDED；`submit_call_count == 1` | 真实 owner | `V02_CHECK [8]`、`B8_NEGATIVE PASS` | **PASS** `TESTED_ISOLATED` |
| **T11** | 作品已提交 + Memory consumer 失败 → 不回滚 | `run_isolated_acceptance.sh [11]` | 真实 owner + 坏 Memory 路径 | 轮次仍 `ACTED`、版本 +1、Activity 仍 ACTIVE、经历 pending、且是 disposition 不是 error | 真实 owner | `V02_CHECK [11]` | **PASS** `TESTED_ISOLATED` |
| **T12** | 纠正错误的作品记忆：旧错误不再影响判断，S01 head 不变 | `check_life_memory_v02.py [5]` | 真实 M0 + MemoryControlLedger | **DELETE / 停止召回已通过**：被删除的经历不再进视图，M0 证据未被改动，被停用/被纠正的记忆在 `blocked=[]` 之外确实被排除（B8-7 在真实旧 profile 上复验过：控制账本健康、纠正文本保留、列表里不再出现）。**原生生活经历的内容替换未实现**：`memory_control.py:132-133` 要求替换事件来自直接用户输入（`speaker != 'user' or source_origin != 'USER_VISIBLE_INPUT'` 即 `ValueError`），生活经历没有用户事件，伪造一条用户消息会违反 E07 | 真实 owner | `MEMORY_CHECK [5]`；`docs/engineering/v02-b8-7-t20-legacy-compat.md §2` | **DEFERRED** — 见下 §缺口 |
| **T13** | 权限撤回：新写请求拒绝，历史作品仍在 | B8-7 安装走通（`INSTALL.md` 的真实记录） | 真实 Supply owner（在线撤回） | **在线撤回真的做过**：`grant:2fce125a99c540f6948f21ed0e51af78`（`COMMIT_MANAGED_ARTIFACT`）先用它写出 v2（`1791684301`），随后被撤回（`revocation_state=REVOKED`、`revoked_at=1791684340`、`revision=2`）；撤回后的写请求被 owner 拒绝（`status=REVOKED, reason=GOVERNANCE_REFUSED`），作品 `artifact:be778d6d32e94796abffdbf392f7a54c` **仍然只有 2 个版本**（`artifact_versions` 里没有第三条，`artifact_operations` 里也没有撤回后的提交），历史与内容仍可读。**当时的口径**：窄端口把 `REVOKED` 归到 `effect_state=UNKNOWN`（B8-7 §4.4），"被拒绝"这件事 owner 说清了、"没有落地"没有以确定失败的形式回报。**RC1-03 复核 HEAD 代码后的现状**：B8-8 给 `_outcome()` 加了 `reason == GOVERNANCE_REFUSED → REFUSED` 这一支（`life_artifact_port.py:440-451`），而撤回授权在 reserve 步返回的正是 `status=REVOKED, reason=GOVERNANCE_REFUSED`（`life_supply.py:1184`），所以**按现在的代码**这次写请求会被分类成 `REFUSED`，不再是 `UNKNOWN`。但**撤回路径在 B8-8 之后没有重跑过**，所以只能说"当前代码会这样分类"，不得写成"撤回路径已验证" | 真实 owner | 只读复核 `life_supply.sqlite`（`permission_grants` / `artifact_versions` / `artifact_operations`）；B8-7 §1、§4.4；RC1-03 代码复核 | **PASS** `TESTED_ISOLATED`（口径已变，未复测） |
| **T14** | 空闲日：零候选、零认知调用、无编造经历 | `check_life_b7 [2][8]`；`check_life_b8 [5]`；`run_e14_chain.sh` 的空窗对照 | 受控时钟 + 真实常驻进程 | 机制：空窗口 `IDLE_NO_CANDIDATE`、`llm_calls=0`、无 step journal、无编造经历、预算不被消耗。真实长窗（`/srv/nya02/resident2`，RC1-03 快照 `2026-10-11T09:56:56Z`）：预算用尽后的 **84** 次检查窗全部 `BLOCKED`（`ROUND_BUDGET_EXHAUSTED`）、0 次调用、0 次动作、0 条编造经历；另有 86 次 `NOT_DUE`（未到检查间隔，0 调用）与 10 次 `DISABLED`（开关未开，0 调用）。**更正**：B8-8 那一版把"预算用尽后 86 次窗口"写成 BLOCKED，实际 86 是 `NOT_DUE` 的条数，两者的名字不能互换 | 无模型 | `B7_CHECK`、`B8_CHECK`；§24 小时观察 | **PASS（机制）** — 真实 24h 未满，不得标 `SOAK_VERIFIED` |
| **T15** | 预算耗尽：无新副作用，续作条件保留 | `check_life_b7 [7]`；`V02_CHECK [9]` | 隔离 | 认知/轮次预算耗尽 → 唤醒 `BLOCKED`（不是"她选择休息"）；作品与 Activity 不动；重启后消耗仍在 | 替身决定 | `B7_CHECK`、`V02_CHECK [9]` | **PASS** `TESTED_ISOLATED` |
| **T16** | 一个 World 合法动作真正改变 owner 状态 | `run_world_move_e2e.sh`；`run_e14_chain.sh stage5` | 真实 World 服务 + 真 gateway/resolver | 两套实例各自复验：`home`→`nearby_street`（revision 1→2，重启后保持），E14 自己的 World 实例同样走通（`WORLD_MOVE_E2E: PASS (move)`，`[3] world_state.location is nearby_street`）；不可达 MOVE 被拒且世界不变（E14：target `airport` → `DEFINITE_PROVIDER_REJECTION`，`revision_after == revision_before == 3`）；UNKNOWN 用原 execution_id 对账、不二次移动 | 真实 World owner | `WORLD_MOVE_ACCEPTANCE PASS`；`E14 S5` | **PASS** `E2E_VERIFIED` |
| **T17** | 未感知的 World 变化不泄漏成已知候选 | `b8_matrix_accept.py [T17]`；`run_e14_chain.sh stage5` | 观测→候选翻译层 + 动作空间 + 真实 World | 只有被感知的目的地成为机会：在 `home` 时 World 只暴露 `nearby_street`（`school` 从不出现），World 真的改变之后（`nearby_street` rev2 → 候选 `{home,school,convenience_store,park,home_goods_store}`；`school` rev3 → 候选 `{nearby_street}`），AG-0 的机会 id 与选择器可见候选**只**来自当前观测（`wopp:move:school:r3:nearby_street`），未感知地点一个都不在 | 无模型 | `b8_matrix_accept.py`；`E14 S5` | **PASS** `TESTED_ISOLATED` |
| **T18** | 重启后自主恢复，无重复副作用 | `b8_assembly_accept.py [6]`；`run_e14_chain.sh chain`（S3） | 隔离 + 真实模型 | E14 S3：进程正常停止（`STOPPED`）后重启同一 profile，开关/预算/已观察 head 保持，同一个 Activity 继续、产出 V3 而不是第二份作品；两轮 Action 与 operation 都不同，没有重复副作用。`b8_negative_accept` 另证 UNKNOWN 按原 id 对账、不复用旧动作 | **真实模型决定**（E14 S3） | `E14 S3`；`B8_ASSEMBLY`、`B8_NEGATIVE` | **PASS** `E2E_VERIFIED` |
| **T19** | 关闭自主开关：新动作停止，聊天与数据正常 | `b8_assembly_accept.py [8]`；`b8_negative_accept.py [N05]`；`V02_CHECK [9]`；`run_e14_chain.sh chain`（S1） | 隔离 | 关闭后新决策 `DISABLED`；Activity/checkpoint 保留；聊天投影与 status 仍工作。E14 S1 真实复现：新 state root 开关未设置时第一窗 `DISABLED`（"autonomous life is off; nothing was considered"，0 次调用、0 次动作），由 operator 入口置位后才开始决策 | 替身决定 | `B8_ASSEMBLY`、`B8_NEGATIVE`、`V02_CHECK [9]`；`E14 S1` | **PASS** `TESTED_ISOLATED` |
| **T20** | 旧 profile 兼容：原命令、记忆、Supply 数据可用 | `run_t20_legacy.sh build` + `t20_verify.sh`（B8-7 / B8-8） | 真实 `v0.1.0-rc9` 导出实例（旧代码建的 profile，非手写 JSON） | **八项全部通过（38 项断言）**：旧聊天/CLI、旧斜杠命令、旧 M0/M1/M37、旧纠正与删除控制、旧 Supply 作品与授权、自主默认关闭、未授权写入被**确定拒绝**、迁移逐字节完整。第 7 项已由 B8-8 修好：owner 的 `GOVERNANCE_REFUSED`（`stage=reserve_use`，落库前）映射为确定拒绝，且只映射这一个理由码；`GOVERNANCE_FINALIZATION_UNCERTAIN` / `ARTIFACT_REFUSED` / 空 reason 仍是 UNKNOWN，判定从不看 head 是否变化 | 真实 owner + 真实旧实例 | `docs/engineering/v02-b8-7-t20-legacy-compat.md §2`；B8-8 在**已提交字节**上复验 `T20: PASS (38 checks passed)` | **PASS** `E2E_VERIFIED` |

## 24 小时观察（B8-6 / B8-6A 的真实记录，只读复核）

观察窗是 `/srv/nya02/resident2/state`（修复后的记录；`/srv/nya02/resident/**` 是修复前那份，保留为失败证据）。
本表的数字是 **RC1-03 只读快照 `2026-10-11T09:56:56Z`**；括号里给出 08:04:02Z 那次的旧值作对照，两次数值
不同只是因为实例还在跑，不是结论变了。RC1-03 **没有停止或重置**这个实例。

| 观测 | 真实值（RC1-03 快照 `2026-10-11T09:56:56Z`） |
|---|---|
| 窗口起点（state root 首次启动） | `2026-10-11T01:41:31+00:00` |
| 本进程起点 / 启动次数 / 进程存活 | `01:46:04+00:00` / `starts=2` / pid 2728 存活（`ps -o etime` = `08:10:52`） |
| 复核时刻的已观察时长 | `29637s ≈ 8.23h`（最后 tick `09:55:28+00:00`，`ticks=183`；08:04:02Z 时是 `22796s / ticks=145`） |
| 真实有效观察范围 | `01:41:31 → 09:55:28`；**唯一空档 53s**（`01:45:11 → 01:46:04` 的重启边界）。逐 tick 复核最大间隔 **197s**（tick 59→60），其余 ≤181s（poll 180s）。没有进程退出后的长空档，也没有"没人跑"的时段 |
| 检查窗数量 / 最大间隔 | 真正评估过的窗 **97**（`counters.check_windows`：10 `DISABLED` + 3 `ACTED` + 84 `BLOCKED`），另有 86 次 `NOT_DUE` 与 1 次事件唤醒，共 184 次唤醒。评估窗之间的最大间隔 **7315s ≈ 2h02m**（`01:44:28 → 03:46:23`，即 `CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=120` 的设计值），其余 ≤181s |
| 有候选 / 无候选 / 关闭 / 预算拒绝 | 4 个窗有材料（3 个检查窗 + 1 次事件，全部 `ACTED`）/ 86 次 `NOT_DUE`（未到间隔）/ 10 次 `DISABLED`（开关未开，0 调用）/ 84 次 `BLOCKED: ROUND_BUDGET_EXHAUSTED`（0 调用） |
| 真实模型调用（决策） | **4**（`counters.model_calls`；3 个检查窗 + 1 次事件唤醒，各 1 次）。`agency_decision_journal.jsonl` 里 4 条 `DECISION_COMMITTED`，`model_run_ref` 都是 `mrun:real:…` |
| 真实模型调用（内容生成） | **4**（4 个 `ACTED` 轮各 1 次 `purpose=artifact_edit`；`_LazyStepSpec` 每轮只 materialise 一次，4 条 AR-0 attempt 都是 `attempt_no=1`）。**不计入** `counters.model_calls` |
| 真实 provider 请求合计 | **8**（4 决策 + 4 内容） |
| 真实动作 / owner 确认版本 | 4 / 4（`actn:13d2647d6284e082`、`actn:abd0f18e246d63aa`、`actn:9bf96e58627f09d7`、`actn:f760bfa433926a3a`；owner 回执 4 个新版本 `83a75499…`、`8c424630…`、`462b901c…`、`bf53a9c5…`） |
| 有材料的窗口 | 4（`01:43:23`、`01:44:28`、`01:45:01`（事件）、`03:46:23`）——全部落在前 2 小时 05 分内 |
| 事件唤醒 | 1 次（`ARTIFACT_CHANGED` on `artifact-version:639d4dac8fe141948d1e639ad4a11102`），该版本由**外部写入**产生（`artifact_operations.operation_key = op:b86a:external:1`），不是她自己的写回 |
| 自唤醒级联 | **没有**。她自己的 4 次写入（`operation_key = life:2247d7e6…`、`life:e7a21c1f…`、`life:50c3f592…`、`life:465adf51…`）一次都没有变成事件；唯一那次事件来自外部写入 |
| UNKNOWN / pending（AR-0） | `pending_unknown_max=0`、`unknown_settlements=0`、`reconciliations=0`、`reconciled_at_start=[]`——重启没有要结算的悬挂动作 |
| UNKNOWN（AR-1，旧代码） | **4 条 `UNKNOWN_EFFECT` / `settlement_status=OPEN`**（`rset:f4354151…`、`rset:adf3e57d…`、`rset:a77912b3…`、`rset:23caad47…`）——这正是 RC1-01 修掉的分类缺口（见 §本批发现 4）。注意 `counters.unknown_settlements=0` 只统计 `status_class=unknown` 的窗，**不反映**这 4 条 OPEN 结算 |
| 重启恢复 / 是否重复执行 | 重启后开关、预算、已观察 head 保持，`starts=2`；journal 重放只重建计数器（`_replay_journal` 不重跑 tick）；4 个动作各产生 1 个版本、没有第二个同名版本，`submit_call_count=1`，没有重复副作用 |
| 预算异常 | 每日 4 轮在 `03:46` 前用完，此后整天只剩 `ROUND_BUDGET_EXHAUSTED`——不是缺陷，但"日预算在头两小时烧完"这件事必须记下来 |
| 实例代码版本 | `/srv/nya02/resident2/app` 的 `CODE_COMMIT` = `e4712418748e26265d76be2011e28646ce0babf9`（B8-6A），**不含** RC1-01/RC1-02 的修复；`git merge-base --is-ancestor e471241 HEAD` 为真（是 HEAD 的祖先但更旧）。所以即使它跑满 24 小时，也不能用来验证 RC1 的代码 |
| 判定 | `soak_24h = SOAK_RUNNING_24H`、`soak_7d = SOAK_PENDING_7D`。**这个标签只由墙钟时长推出，不作为"24 小时已满足"的证据**；按真实记录（有效观察 8.23h、唯一空档 53s、进程仍活）判定仍是 **RUNNING**，不是 PASS；7 天不提前判定 |

修复前那份记录（`/srv/nya02/resident/**`，B8-6 代码）保留了自唤醒级联的真实形态：4 次 `EVENT` 唤醒、
4 次动作，作品在 `1791681392 → 1791681442` 的 50 秒内连出 4 个版本（`life:54e7ae54…`、
`life:052b1d8a…`、`life:3212fe1e…`、`life:697fe2fd…`）——这正是 B8-6A 修掉的"她自己的写成了自己的事件"。

## 缺口（不得当成 PASS）

- **T12 `DEFERRED`**：现有 Memory Control 只支持**同一会话内用户撰写事件**的 CORRECT；
  `memory_control.py:132-133` 要求替换事件来自用户。原生生活经历没有用户事件，伪造一条用户消息
  就会违反 E07，所以本批不实现、不伪造。已通过的是 DELETE/停止召回（旧错误不再影响判断，
  S01 head 不变，B8-7 在真实旧 profile 上复验）。缺口：生活经历的 CORRECT 需要一个新增的、
  受治理的纠正入口（属独立工作包，且必须写入发布限制）。
- **T20 第 7 项（B8-8 已修）**：owner 的 `GOVERNANCE_REFUSED`（`stage=reserve_use`，落库前）现在映射为确定拒绝；`GOVERNANCE_FINALIZATION_UNCERTAIN`、`ARTIFACT_REFUSED`、空 reason 仍保持 UNKNOWN，判定从不看 head 是否变化。
- **T14**：只证明零调用**机制**；真实一整天的窗口尚未跑满 24 小时，未完成前不得标 `SOAK_VERIFIED`。
- **E14 S6 的真实模型那一轮（RC1-02 已补做）**：B8-8 那一轮因为调用上限（6 次）被 S2/S3/S4 用完而没有做，
  当时也没有手写替代答案。RC1-02 花了 1 次供应商调用补做：插件自己的 `select_context()` 注入了真实投影
  （activity `actv_7a3f447970802f7f`、作品 `artifact:db42c367…`、已确认版本 `artifact-version:d2a38b43…`），
  模型答"最近在继续写《长测的手记》。"，没有把 UNKNOWN 说成完成、没有编造版本，前后 Activity / head /
  Memory / budget 逐字节相同。**证据级别必须照抄**：这是"真实插件上下文 → 真实模型消费 → 对生活事实描述
  正确"，**不是**完整的 Hermes CLI / Telegram 收发链路，发布说明里不得写成端到端聊天已验证。
- **E14 S3 的 checkpoint 前移**：LR-5 的 checkpoint 是**作品粒度**（`checkpoint_ref` = `artifact:<id>`），
  所以 V2 那一步记录 `CHECKPOINT_AND_PROGRESS_RECORDED` + `CHECKPOINT_ACTIVITY_SYNC_CONFIRMED`
  （Activity revision 1→2），V3 那一步只记录 `PROGRESS_EVIDENCE_EVALUATED`、`progress_state=UNCHANGED`，
  Activity 的 `last_action_ref` 仍指向第一轮的 `actn:6696072f3fbf8df8`。这是真实记录，不是 PASS 的替代品。

## 发现过、现在仍然成立的源码/工具链问题（RC1-03 逐条标当前状态）

下面每条都保留发现时的事实，并在前面标出 RC1-01/RC1-02/RC1-03 之后的状态。

1. **[已修 · RC1-02]** `autonomous_life_resident.start()` 报 `model.content_writer=false` 恒假：它读
   `assembled.get("writer")`，而 `assemble_autonomous_life()` 的返回字典里没有这个键（B8-7 §4.2 已记录；
   当时内容确实由真实模型写出，`artifact_versions.operation_key = life:…`）。现在改读已装配 cycle 自己的
   `writer`，配了真实模型时报 `true`；没有可用写作者时装配在 `NO_CONTENT_WRITER` 处 BLOCKED，且返回记录
   里没有 `model` 键。修的是报告，生成链未动。
2. **[已修 · RC1-02]** `counters.defer` 恒为 0：`autonomous_life_resident._count()` 判的是
   `outcome == "DEFER"`，而 `AutonomousLifeCycle` 用的词是 `DEFERRED`，于是 E14 S4 两次真实 DEFER 在
   `outcomes` 里是 `DEFERRED: 2`、在 `defer` 里仍是 0。现在从 cycle 自己的常量取值，真实 E14 journal 重放
   得 `defer=2`。
3. **[仍未修]** LR-5 checkpoint 粒度：作品粒度，连续版本不会推动 checkpoint。
4. **[已修 · RC1-01]** AR-1 settlement 的 `outcome_kind`：两次成功写入都记成 `UNKNOWN_EFFECT` /
   `settlement_status=OPEN`，而 owner 回执是明确的 `OK`。修法是 owner 确认版本后用同一个官方读端口重读该
   版本（`resolve_operation` + `read_head`），四条全中才记成已验证，AR-1 再按自己的规则结算；AR-1 的判定
   没有放宽。**保留的边界**：一个已经不再是 head 的旧版本可能拿不到这份观察证据，正确地保持 OPEN 而不是
   被猜成成功。真实长测（`/srv/nya02/resident2`，旧代码）里的 4 条 `UNKNOWN_EFFECT / OPEN` 是**修复前的
   记录**，保留不改写。
5. **[仍未修 · RC1-03 只加了显示标签]** 每日预算与 `counters.model_calls` 只统计决策调用：一次成功写入
   还会再发生一次内容生成调用（`purpose=artifact_edit`），不计入该数字。RC1-03 量到的真实数是 4 次决策
   + 4 次内容 = **8 次 provider 请求**，而 `counters.model_calls = 4`。只在
   `resident_summary.json` 里加了 `model_calls_detail`（`cognition_calls` / `content_generation_calls` /
   `total_requests` / `daily_budget_counts: "cognition calls only"`）把两类调用分开命名，**没有加新的预算
   框架**，预算语义未改。

## 回归

`scripts/test.py` 的 owned 套件：`closeout / native / memory / supply / world(259) / life /
life-v02 / life-memory-v02 / life-b7` → `FAILED_SUITES: none`；`B7_CHECK`、`B8_CHECK`、
`B8_NEGATIVE`、`B8_ASSEMBLY`、`B8_MATRIX` 全 PASS；`V02_CHECK`/`ISOLATED ACCEPTANCE` PASS；
`MEMORY_CHECK` PASS。真实 World：`WORLD_MOVE_ACCEPTANCE PASS`。
B8-8 新增：`run_e14_chain.sh lifecycle` → `E14_LIFECYCLE: PASS`；`run_e14_chain.sh chain`
的 S1–S4 真实跑通（S5/S6 单独复跑），`E14_CHAIN` 的逐阶段证据见交接文档。
`host-boundaries`、`cognition-faults`、`Hermes` 三个套件在 RC1-02 之前是已记录的环境性失败
（`KNOWN_ISSUES.md`），B8-8 那一轮没有重跑、也没有掩盖。**RC1-03 把它们逐条查清了**（只装测试依赖的
隔离 venv，按原命令重跑，未删测试、未改产品行为、未加无条件跳过）：

| 套件 | 真实原因 | 修法 | 重跑结果 |
|---|---|---|---|
| `cognition-faults` | 缺 `pytest` | venv 装 `pytest` | **1 passed** |
| `Hermes`（4 个 context-engine 文件） | 缺 `python-dotenv`（连带 `requests` / `httpx[socks]` / `pytest-asyncio` / `ruamel.yaml`） | venv 装依赖 | **29 passed, 0 failed** |
| `host-boundaries` | ① 缺 `httpx` / `psutil` / `PyYAML`；② **工作区 CRLF**（根目录无 `.gitattributes`）：`bootstrap.sh` 在 bash 下 `set: pipefail / : invalid option name`（6 个）、`patches/baseline.sha256` 失配（1 个）、封存组件 `sealed_component_mismatch`（2 个）；③ 真实缺陷 1 个 | 装依赖；CRLF 用 `git archive HEAD` 导出的 LF 树复现 | 装上依赖后 **8 failures / 2 errors**（全部是 CRLF）；LF 树上只剩 **1 failure**：`test_website_and_repository_installers_are_identical`（`scripts/bootstrap.sh` = `v0.1.0-rc6` vs `website/install.sh` = `v0.1.0-rc9`，差 2 行） |

封存组件那条已定量核对：把 `components/native/native_recall/production_consumption_evaluator_v0_3.py`、
`production_consumption_evaluator_v0.py`、`CANDIDATE_CONSUMPTION_POLICY_V0_3.json` 的 CRLF 归一成 LF 后，
sha256 与 `production_resolver.SEALED_SHA256` **逐字符相等**（`readonly_recall_adapter.py` 的 blob 本来就是
CRLF，所以它一直匹配）。即：**哈希失配 100% 来自检出，不来自源码**。
