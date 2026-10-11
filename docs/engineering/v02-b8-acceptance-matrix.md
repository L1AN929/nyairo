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
| **T13** | 权限撤回：新写请求拒绝，历史作品仍在 | B8-7 安装走通（`INSTALL.md` 的真实记录） | 真实 Supply owner（在线撤回） | **在线撤回真的做过**：`grant:2fce125a99c540f6948f21ed0e51af78`（`COMMIT_MANAGED_ARTIFACT`）先用它写出 v2（`1791684301`），随后被撤回（`revocation_state=REVOKED`、`revoked_at=1791684340`、`revision=2`）；撤回后的写请求被 owner 拒绝（`status=REVOKED, reason=GOVERNANCE_REFUSED`），作品 `artifact:be778d6d32e94796abffdbf392f7a54c` **仍然只有 2 个版本**（`artifact_versions` 里没有第三条，`artifact_operations` 里也没有撤回后的提交），历史与内容仍可读。**口径保留**：窄端口把 `REVOKED` 归到 `effect_state=UNKNOWN`（B8-7 §4.4），"被拒绝"这件事 owner 说清了、"没有落地"没有以确定失败的形式回报 | 真实 owner | 只读复核 `life_supply.sqlite`（`permission_grants` / `artifact_versions` / `artifact_operations`）；B8-7 §1、§4.4 | **PASS** `TESTED_ISOLATED` |
| **T14** | 空闲日：零候选、零认知调用、无编造经历 | `check_life_b7 [2][8]`；`check_life_b8 [5]`；`run_e14_chain.sh` 的空窗对照 | 受控时钟 + 真实常驻进程 | 机制：空窗口 `IDLE_NO_CANDIDATE`、`llm_calls=0`、无 step journal、无编造经历、预算不被消耗。真实长窗（`/srv/nya02/resident2`）：预算用尽后的 86 次窗口全部 `BLOCKED`（`ROUND_BUDGET_EXHAUSTED`）、0 次调用、0 次动作、0 条编造经历 | 无模型 | `B7_CHECK`、`B8_CHECK`；§24 小时观察 | **PASS（机制）** — 真实 24h 未满，不得标 `SOAK_VERIFIED` |
| **T15** | 预算耗尽：无新副作用，续作条件保留 | `check_life_b7 [7]`；`V02_CHECK [9]` | 隔离 | 认知/轮次预算耗尽 → 唤醒 `BLOCKED`（不是"她选择休息"）；作品与 Activity 不动；重启后消耗仍在 | 替身决定 | `B7_CHECK`、`V02_CHECK [9]` | **PASS** `TESTED_ISOLATED` |
| **T16** | 一个 World 合法动作真正改变 owner 状态 | `run_world_move_e2e.sh`；`run_e14_chain.sh stage5` | 真实 World 服务 + 真 gateway/resolver | 两套实例各自复验：`home`→`nearby_street`（revision 1→2，重启后保持），E14 自己的 World 实例同样走通（`WORLD_MOVE_E2E: PASS (move)`，`[3] world_state.location is nearby_street`）；不可达 MOVE 被拒且世界不变（E14：target `airport` → `DEFINITE_PROVIDER_REJECTION`，`revision_after == revision_before == 3`）；UNKNOWN 用原 execution_id 对账、不二次移动 | 真实 World owner | `WORLD_MOVE_ACCEPTANCE PASS`；`E14 S5` | **PASS** `E2E_VERIFIED` |
| **T17** | 未感知的 World 变化不泄漏成已知候选 | `b8_matrix_accept.py [T17]`；`run_e14_chain.sh stage5` | 观测→候选翻译层 + 动作空间 + 真实 World | 只有被感知的目的地成为机会：在 `home` 时 World 只暴露 `nearby_street`（`school` 从不出现），World 真的改变之后（`nearby_street` rev2 → 候选 `{home,school,convenience_store,park,home_goods_store}`；`school` rev3 → 候选 `{nearby_street}`），AG-0 的机会 id 与选择器可见候选**只**来自当前观测（`wopp:move:school:r3:nearby_street`），未感知地点一个都不在 | 无模型 | `b8_matrix_accept.py`；`E14 S5` | **PASS** `TESTED_ISOLATED` |
| **T18** | 重启后自主恢复，无重复副作用 | `b8_assembly_accept.py [6]`；`run_e14_chain.sh chain`（S3） | 隔离 + 真实模型 | E14 S3：进程正常停止（`STOPPED`）后重启同一 profile，开关/预算/已观察 head 保持，同一个 Activity 继续、产出 V3 而不是第二份作品；两轮 Action 与 operation 都不同，没有重复副作用。`b8_negative_accept` 另证 UNKNOWN 按原 id 对账、不复用旧动作 | **真实模型决定**（E14 S3） | `E14 S3`；`B8_ASSEMBLY`、`B8_NEGATIVE` | **PASS** `E2E_VERIFIED` |
| **T19** | 关闭自主开关：新动作停止，聊天与数据正常 | `b8_assembly_accept.py [8]`；`b8_negative_accept.py [N05]`；`V02_CHECK [9]`；`run_e14_chain.sh chain`（S1） | 隔离 | 关闭后新决策 `DISABLED`；Activity/checkpoint 保留；聊天投影与 status 仍工作。E14 S1 真实复现：新 state root 开关未设置时第一窗 `DISABLED`（"autonomous life is off; nothing was considered"，0 次调用、0 次动作），由 operator 入口置位后才开始决策 | 替身决定 | `B8_ASSEMBLY`、`B8_NEGATIVE`、`V02_CHECK [9]`；`E14 S1` | **PASS** `TESTED_ISOLATED` |
| **T20** | 旧 profile 兼容：原命令、记忆、Supply 数据可用 | `run_t20_legacy.sh build` + `t20_verify.sh`（B8-7 / B8-8） | 真实 `v0.1.0-rc9` 导出实例（旧代码建的 profile，非手写 JSON） | **八项全部通过（38 项断言）**：旧聊天/CLI、旧斜杠命令、旧 M0/M1/M37、旧纠正与删除控制、旧 Supply 作品与授权、自主默认关闭、未授权写入被**确定拒绝**、迁移逐字节完整。第 7 项已由 B8-8 修好：owner 的 `GOVERNANCE_REFUSED`（`stage=reserve_use`，落库前）映射为确定拒绝，且只映射这一个理由码；`GOVERNANCE_FINALIZATION_UNCERTAIN` / `ARTIFACT_REFUSED` / 空 reason 仍是 UNKNOWN，判定从不看 head 是否变化 | 真实 owner + 真实旧实例 | `docs/engineering/v02-b8-7-t20-legacy-compat.md §2`；B8-8 在**已提交字节**上复验 `T20: PASS (38 checks passed)` | **PASS** `E2E_VERIFIED` |

## 24 小时观察（B8-6 / B8-6A 的真实记录，只读复核）

观察窗是 `/srv/nya02/resident2/state`（修复后的记录；`/srv/nya02/resident/**` 是修复前那份，保留为失败证据）。

| 观测 | 真实值 |
|---|---|
| 窗口起点（state root 首次启动） | `2026-10-11T01:41:31+00:00` |
| 本进程起点 / 启动次数 | `01:46:04+00:00` / `starts=2` |
| 复核时刻的已观察时长 | `22796s ≈ 6.33h`（复核时刻 `2026-10-11T08:04:02Z`，仍在运行，`ticks=145`） |
| 真实模型调用 | **4**（4 个有材料的窗口，各 1 次决策；内容生成调用按 B8-7 §4.3 未计入计数器） |
| 真实动作 / owner 确认版本 | 4 / 4（`actn:13d2647d6284e082`、`actn:abd0f18e246d63aa`、`actn:9bf96e58627f09d7`、`actn:f760bfa433926a3a`） |
| 有材料的窗口 | 4（`01:43:12`、`01:44:13`、`01:44:48`、`03:46:06`）——全部落在前 2 小时 05 分内 |
| 无材料/未到期窗口 | 86 次 `NOT_DUE`（0 次调用） |
| 预算拒绝 | 46 次 `BLOCKED: budget refused the wake: ROUND_BUDGET_EXHAUSTED`（`05:46:25` 起，0 次调用） |
| 事件唤醒 | 1 次（`ARTIFACT_CHANGED` on `artifact-version:639d4dac8fe141948d1e639ad4a11102`），该版本由**外部写入**产生（`artifact_operations.operation_key = op:b86a:external:1`），不是她自己的写回 |
| 自唤醒级联 | **没有**。唤醒间隔 `180s × 113`、启动期 `10s × 16`，其余为重启边界；没有任何一次唤醒由她自己的写入触发 |
| UNKNOWN / pending | `pending_unknown_max=0`、`unknown_settlements=0`、`reconciliations=0` |
| 重启恢复 | 重启后开关/预算/已观察 head 保持，`starts=2`，没有重复副作用 |
| 预算异常 | 每日 4 轮在 `03:46` 前用完，此后一整天（含复核时）只剩 `ROUND_BUDGET_EXHAUSTED`——不是缺陷，但"日预算在头两小时烧完"这件事必须记下来 |
| 判定 | `soak_24h = SOAK_RUNNING_24H`、`soak_7d = SOAK_PENDING_7D` |

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
- **E14 S6 的真实模型那一轮**：`presence_context` 与插件上下文钩子已用真实运行态验证，
  但"模型能不能真的说出已确认的作品"这一句需要再花 1 次供应商调用；B8-8 的调用上限（6 次）
  已被 S2/S3/S4 用完，因此本轮**没有**做那一轮对话，也没有手写替代答案（见
  `docs/engineering/v02-autonomous-life-implementation.md §8`）。
- **E14 S3 的 checkpoint 前移**：LR-5 的 checkpoint 是**作品粒度**（`checkpoint_ref` = `artifact:<id>`），
  所以 V2 那一步记录 `CHECKPOINT_AND_PROGRESS_RECORDED` + `CHECKPOINT_ACTIVITY_SYNC_CONFIRMED`
  （Activity revision 1→2），V3 那一步只记录 `PROGRESS_EVIDENCE_EVALUATED`、`progress_state=UNCHANGED`，
  Activity 的 `last_action_ref` 仍指向第一轮的 `actn:6696072f3fbf8df8`。这是真实记录，不是 PASS 的替代品。

## 本批发现、但未修的源码问题

1. **`autonomous_life_resident.start()` 报 `model.content_writer=false` 恒假**：它读
   `assembled.get("writer")`，而 `assemble_autonomous_life()` 的返回字典里没有这个键
   （B8-7 §4.2 已记录；本轮内容确实由真实模型写出，`artifact_versions.operation_key = life:…`）。
2. **`counters.defer` 恒为 0**：`autonomous_life_resident._count()` 判的是 `outcome == "DEFER"`，
   而 `AutonomousLifeCycle` 用的词是 `DEFERRED`，于是 E14 S4 两次真实 DEFER 在
   `outcomes` 里是 `DEFERRED: 2`、在 `defer` 里仍是 0。
3. **LR-5 checkpoint 粒度**：见上（作品粒度，连续版本不会推动 checkpoint）。
4. **AR-1 settlement 的 `outcome_kind`**：两次成功写入都记成 `UNKNOWN_EFFECT` / `settlement_status=OPEN`，
   而 owner 回执是明确的 `OK`。后果正确（Memory 入账、LR-5 记录都成立），但分类口径与回执不一致，
   本批只记录、不改。

## 回归

`scripts/test.py` 的 owned 套件：`closeout / native / memory / supply / world(259) / life /
life-v02 / life-memory-v02 / life-b7` → `FAILED_SUITES: none`；`B7_CHECK`、`B8_CHECK`、
`B8_NEGATIVE`、`B8_ASSEMBLY`、`B8_MATRIX` 全 PASS；`V02_CHECK`/`ISOLATED ACCEPTANCE` PASS；
`MEMORY_CHECK` PASS。真实 World：`WORLD_MOVE_ACCEPTANCE PASS`。
B8-8 新增：`run_e14_chain.sh lifecycle` → `E14_LIFECYCLE: PASS`；`run_e14_chain.sh chain`
的 S1–S4 真实跑通（S5/S6 单独复跑），`E14_CHAIN` 的逐阶段证据见交接文档。
`host-boundaries`、`cognition-faults`、`Hermes` 三个套件仍是已记录的环境性失败（`KNOWN_ISSUES.md`），
本轮没有重跑、也没有掩盖。
