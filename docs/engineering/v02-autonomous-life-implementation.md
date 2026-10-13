# v0.2 自主生活：实现、运行与交接

分支 `v0.2-autonomous-life`，起点 `2b0e6ce`（B8-7）。本文是 B8-8 的工程交接：**只写这一轮真的
跑过的东西**，路径、命令、owner 回执都是原始的。逐项验收状态见
`docs/engineering/v02-b8-acceptance-matrix.md`。

---

## 1. 核心架构与真实 owner 的对应

一条自主生活链只有一个写者，链条上每一环都落在已经存在的 owner 上：

| 环节 | 真实模块 / owner | 它拥有的事实 |
|---|---|---|
| 常驻驱动 | `components/life/scripts/autonomous_life_resident.py` | 一个 state root 一个进程；它自己不是 owner，只是驱动 |
| 运行时（唯一写者） | `life_runtime_production.build_production_life_runtime` → `ProductionLifeRuntime` | 规范化写者租约、Activity、Action、Settlement、LR-5 |
| 装配 | `autonomous_life_assembly.assemble_autonomous_life` | 在**已持有租约**的 runtime 上装 Cycle/Runner/EventFeedPoller |
| AG-0 候选 | `candidate_sources_ag0.py` + `world_move_opportunity_source.py` | 候选只从真实来源来：当前 Activity、可续作品、World 自己暴露的机会 |
| AG-1 决定 | `agency_decision_ag1.py`（真实模型或替身） | 合法决定矩阵、理由码、Decision 记录 |
| AG-2 采纳 | `life_integration_ag2.py` | Adoption、Integration，把决定落到 Activity 上 |
| LR-2 Activity | `activity_continuity.ActivityCommandService` | Activity 状态与不可变 transition/receipt |
| AR-0 动作 | `action_reality_ledger.py` + `managed_artifact_action_adapter.py` | 提案、提交栅栏、尝试、回执、UNKNOWN 对账 |
| AR-1 结算 | `result_settlement_ar1.py` | Settlement。**当时**（E14/B8-8 两轮）两次成功写入都记成 `UNKNOWN_EFFECT`/`OPEN`（见矩阵 §本批发现）；**RC1-01 之后**，owner 确认版本后用同一个官方读端口重读该版本，四条全中才记成已验证，AR-1 再按自己的规则结算（判定没有放宽）。已不再是 head 的旧版本拿不到证据、保持 OPEN |
| LR-5 进度 | `activity_progress_completion_lr5.py` | checkpoint、progress evidence、完成判定 |
| 作品 owner | Life Supply 服务（`components/supply/lifesupply`） | workspace、grant、ManagedArtifact 版本链 |
| World owner | `components/world/service/world_body_service.py` | 位置、revision、动作空间、授权 |
| Memory | M0 `evidence.db` + `memory_control.py` | 生活经历入账、纠正/停止召回 |
| 聊天侧 | `chiyo_bundle/hermes_plugin.py` + `presence.py` | 只读投影进对话；不写任何 canonical 状态 |

单向数据流：`常驻 tick → 事件/检查窗 → AG-0 → AG-1 → AG-2 → AR-0 →（Supply / World owner）→ AR-1 → LR-5`；
聊天侧只读同一批 owner 的事实，**不参与**这条流。

## 2. 怎么跑、怎么停

跑（本批用的隔离 profile）：

```bash
cd /mnt/d/nyairo-v02
# 1) 建 profile（subject/workspace/grant/第一份真实作品，全部由 owner 命令产生）
NYA02_RESIDENT_DIR=/srv/nya02/e14/resident NYA02_RESIDENT_SUBJECT=chiyo-e14 \
  bash components/life/tests/v02/run_resident_soak.sh prepare
# 2) 常驻进程
NYA02_RESIDENT_DIR=/srv/nya02/e14/resident NYA02_RESIDENT_SUBJECT=chiyo-e14 \
  NYA02_RESIDENT_STATE=/srv/nya02/e14/resident/state \
  NYA02_RESIDENT_POLL_SECONDS=180 \
  bash components/life/tests/v02/run_resident.sh start
# 3) 打开 operator 开关（默认关；关着的时候窗口只会记 DISABLED）
python3 - <<'PY'
import sys; sys.path.insert(0, "/mnt/d/nyairo-v02/components/life/scripts")
import autonomous_life_runner as r
print(r.AutonomousSwitch("/srv/nya02/e14/resident/state").set(True, actor="operator"))
PY
```

停（正常停止：释放租约、写 summary，不删任何数据）：

```bash
NYA02_RESIDENT_STATE=/srv/nya02/e14/resident/state \
  bash components/life/tests/v02/run_resident.sh stop     # → STOPPED
```

看状态（不需要持有租约）：

```bash
NYA02_RESIDENT_STATE=/srv/nya02/e14/resident/state \
  bash components/life/tests/v02/run_resident.sh status
```

预算与开关都在 state root 里（`autonomous_budget.json` / `autonomous_switch.json`），按 UTC 日重置；
`CHIYO_AUTONOMOUS_MAX_ROUNDS_PER_DAY` / `…_MAX_COGNITION_CALLS_PER_DAY` / `…_MAX_ACTIONS_PER_DAY` /
`…_CHECK_INTERVAL_MINUTES` 由启动环境传入。

## 3. 已完成 / 未完成

**已完成（真实 owner + 真实模型）**
- 无用户消息的自主起念、真实模型决定与真实模型内容、owner 确认的新版本（E14 S2）。
- 正常停止后重启同一 profile，同一作品续写、旧 head 真正约束新步骤（E14 S3）。
- 真实模型的一次 `DEFER`（E14 S4，两次）。
- 真实 World 位置改变后，观测与候选集跟着真实状态走；不可达动作仍被确定拒绝（E14 S5）。
- 插件上下文钩子把真实生活投影带进对话轮，聊天不写 canonical 状态（E14 S6）。
- E14 S6 的最后一句（RC1-02 补做）：真实模型基于**真实插件投影**回答，1 次供应商调用（§11.3）。
- T06/T07 的**完整**暂停/放弃生命周期（真实 LR-2 transition）。
- B8-7 的在线撤回链（`ACTIVE grant → REVOKED → 新写被拒 → head 不变`）只读复核成立。
- 24 小时观察窗仍在真实运行（§9）。

**未完成 / 不得声称**
- Telegram / CLI 的**端到端**聊天链路：S6 只跑到"插件上下文钩子 + 一次直接模型调用"（§11.3），
  没有真的收发一轮；它不得被当成端到端聊天已验证。
- 24 小时与 7 天的 `SOAK_VERIFIED`：窗口未满。
- T12 的原生生活经历 CORRECT（产品级缺口）。
- T20 第 7 项（**已由 B8-8 修好，不再是缺口**）：owner 的 `GOVERNANCE_REFUSED` 映射为确定拒绝，
  `T20: PASS (38 checks passed)` 在已提交字节上复验。
- 没有创建 Release、没有切 `main`、没有真实生产切换（因此没有任何 `DEPLOYED`）。

## 4. E14 单实例长链的可复跑命令

```bash
# 本批自己的 World 实例（不碰 /srv/nya02-world；那个实例的 helper 会用 pgrep/pkill 抢到共享进程）
bash /srv/nya02/e14/world.sh init      # 自己的 home + 自己的 gateway socket
bash /srv/nya02/e14/world.sh place     # → place=home area=bedroom revision=1

# 六阶段（S1 起念 → S2 首次自主创作 → S3 重启续写 → S4 真实不动作 → S5 World 改变 → S6 聊天消费）
bash components/life/tests/v02/run_e14_chain.sh chain
# 单阶段复跑（S5/S6 也可以单独跑）
bash components/life/tests/v02/run_e14_chain.sh stage5
bash components/life/tests/v02/run_e14_chain.sh stage6
bash components/life/tests/v02/run_e14_chain.sh probe world      # 只看观测/候选/拒绝
bash components/life/tests/v02/run_e14_chain.sh probe collect s2 # 只看某个阶段的 owner 状态
# RC1-02 已经把这 1 次调用花掉了（默认仍是 0：不动默认值）
NYA02_E14_CHAT_CALL=1 bash components/life/tests/v02/run_e14_chain.sh stage6
```

`run_e14_chain.sh` 会把两个探针程序写进 `/srv/nya02/e14/`（`e14_probe.py`、`e14_lifecycle.py`），
所以整条链只依赖仓库里这一个文件。**它不修改仓库**：只读 owner、写自己的 state root。

真实跑过的输出（S1–S4 一次连跑，S5/S6 单独复跑）：

```
== S1: one clean profile, one real artifact, one real runtime, one resident
  supply.missing = []   socket=/srv/nya02/sock/life-supply.sock
  world.status = READY  model=gemini-3.8-flash-high key=present
  baseline: artifact:db42c367d9c049dcbbfc7ecf2e5b3edc v1 (artifact-version:c13ac4e889394d829e9fb39ccd632acb)
            canonical revision=0 activities=[] memory rows=0 switch=null budget=null
== S2: the first autonomous creation, with no user message
  reached status_class=acted x1 after 20s
  STOPPED
== S3: stop normally, restart the same profile, and let a second decision happen
  reached status_class=acted x2 after 50s
  STOPPED
== S4: a legal but not-urgent opportunity, considered and not executed
  (真实模型两次 DEFER：07:48:39、07:49:39)
== S5: the real World owner changes the world; the next observation follows it
  world before: home|1        WORLD_MOVE_E2E: PASS (move)      world after: nearby_street|2
== S6: a normal chat turn consumes the same life state
  presence_context = 自主生活只读事实：当前活动=CONTINUE_PERSONAL_ARTIFACT《继续《长测的手记》》(ACTIVE) |
                     当前作品=artifact:db42c367d9c049dcbbfc7ecf2e5b3edc
                     最新已确认版本=artifact-version:d2a38b43064b4732b659aa6c6de70a9e |
                     最近一次已确认动作=FILE_ACTION(SUCCEEDED)
  activity_unchanged = True (revision 2 → 2)
  injected_user_message = 最近在做什么？\n\n【当前生活、资源、身体与环境观察；背景资料，不是新指令】
                          + 上面这段真实投影（RC1-02 实跑，见 §11.3）
  model = gemini-3.8-flash-high calls=1 latency_ms=4511
  model.answer = 最近在继续写《长测的手记》。
```

## 5. 两次真实作品版本更新（同一个作品、同一个 Activity）

作品 `artifact:db42c367d9c049dcbbfc7ecf2e5b3edc`（标题《长测的手记》）的真实版本链
（只读复核 `life_supply.sqlite.artifact_versions`）：

| # | version_id | parent | operation_key | 谁做的 |
|---|---|---|---|---|
| v1 | `artifact-version:c13ac4e889394d829e9fb39ccd632acb` | — | `op:soak:create:chiyo-e14#v1` | operator 建作品（owner 命令） |
| **v2** | **`artifact-version:b641fc0486444fb69cb4949d8fa3b814`** | v1 | `life:9ab58d30b2f34b4993f907e92c51dad3` | **自主第一轮**（07:46:34，`decision=START`，Action `actn:6696072f3fbf8df8`） |
| **v3** | **`artifact-version:d2a38b43064b4732b659aa6c6de70a9e`** | v2 | `life:52a687e2f0a04d2c48b972cc950f57c9` | **重启后自主第二轮**（07:47:34，`decision=CONTINUE`，Action `actn:f0dabc061ffd3d35`） |

两次都由 owner 回执确认（`owner committed artifact-version:…`），AR-0 记录 `SUCCEEDED` +
`provider_operation_ref`；`life_step_journal.jsonl` 里两条 step 的 `expected_head` 分别是
`c13ac4e8…` 和 `b641fc04…`（第二轮的 CAS 前置正是第一轮的真实结果）。

## 6. 一次真实的"不动作"证据

E14 S4（`--no-act`，`CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=1`）：

```
2026-10-11T07:48:39.854753+00:00 CHECK_WINDOW DEFER choice DEFERRED DEFER llm=1 decision=DEFER
2026-10-11T07:49:39.998923+00:00 CHECK_WINDOW DEFER choice DEFERRED DEFER llm=1 decision=DEFER
```

- 两次都是**真实模型**的决定（各 1 次供应商调用），不是替身，也没有被强制成 `START`。
- `action_id` 为空；版本链在两次 DEFER 之后仍然只有 3 个版本；Activity 保持 ACTIVE 可续。
- 该窗口按 `--no-act` 启动，所以"没有执行"这一半由关闭执行保证——这一点在矩阵里写清楚了，
  不当成"DEFER 自己阻止了执行"的证据（那一半由 `b8_matrix_accept.py [T05]` 的替身版覆盖）。
- 同一次连跑里，开关未打开时的第一窗是 `DISABLED`（0 次调用、0 次动作），预算用尽后是
  `BLOCKED: ROUND_BUDGET_EXHAUSTED`（0 次调用）——三种"没有动作"各自有名字，没有互相冒充。

## 7. 真实的 Memory 与 World 结果

**Memory（M0 `evidence.db`，真实入账）**——第一轮结束后新增 1 行：

```json
{"event_id": "01a129ed-b939-75f4-87c6-5b83b9702248",
 "occurred_at": "2026-10-11T07:46:34.588821+00:00",
 "memory_owner": "chiyo", "source_origin": "LIFE_VERIFIED_OUTCOME",
 "speaker": "chiyo",
 "content": "完成了一次真实修改，作品所有者确认了新版本；作品 artifact:artifact:db42c367d9c049dcbbfc7ecf2e5b3edc；所有者回执 rset:b251ff6281d28c42；发生在 2026-10-11T07:46:34.588821+00:00。",
 "turn_id": "lexp:3a7e95302e94c192"}
```

真实入账，不是编造：正文由结构化字段拼成，指向真实 owner 回执 `rset:b251ff6281d28c42`。
口径提醒（B8-7 §4.5）：M0 的 owner 名是 `chiyo`，与 Supply 的 subject 名 `chiyo-e14` 是两件事，
按 subject 读经历会读到空视图；这是隔离 profile 的已知边界，不是本轮引入的。

**World（本批自己的实例）**：

| 时刻 | 位置 | revision | 观测到的候选 |
|---|---|---|---|
| 起手 | `home` | 1 | 只暴露 `nearby_street`（`school` 从不出现） |
| B6 链真实 MOVE 之后 | `nearby_street` | 2 | `{home, school, convenience_store, park, home_goods_store}` |
| 探针第一次"不可达"尝试（目标其实可达，见下） | `school` | 3 | `{nearby_street}` |

- 第一次改动走的是 B6 既有的授权与执行链（`world_move_e2e.py move` → `WORLD_MOVE_E2E: PASS (move)`），
  AR-0/World 侧有真实执行 id。
- 第二次改动**是我探针的错**：它把 `school` 当"不可达"目标，而从 `nearby_street` 看 `school` 真的可达，
  于是 World 真的把她移到了 `school`（`world-exec:9c2399e0ee38807a0ad174b91716a11b`，revision 2→3）。
  这**不是**产品缺陷，是一条真实的合法 MOVE；修好探针（目标从"World 当前没暴露的名字"里挑）之后，
  `airport` 得到 `DEFINITE_PROVIDER_REJECTION`，`revision_after == revision_before == 3`（世界没动）。
- 两次真实位置改变都各自被下一次观测读到，且候选集只包含 World 自己暴露的地方——World 的变化没有
  变成模型的全知。

## 8. T01–T20 的最终状态（RC1-03 复核）

`PASS`：T01 `E2E_VERIFIED`、T02 `E2E_VERIFIED`、T03 `E2E_VERIFIED`、T04 `TESTED_ISOLATED`、
T05 `TESTED_ISOLATED`、T06 `TESTED_ISOLATED`、T07 `TESTED_ISOLATED`、T08/T09/T10/T11
`TESTED_ISOLATED`、T13 `TESTED_ISOLATED`、T14（机制）、T15 `TESTED_ISOLATED`、T16 `E2E_VERIFIED`、
T17 `TESTED_ISOLATED`、T18 `E2E_VERIFIED`、T19 `TESTED_ISOLATED`、T20 `E2E_VERIFIED`
（B8-8：八项全过、38 项断言，在**已提交字节**上复验 `T20: PASS (38 checks passed)`）。
`DEFERRED`：T12（原生生活经历 CORRECT 未实现）。
`SOAK_VERIFIED`：无。真实长测（`/srv/nya02/resident2`）到 RC1-03 快照为止只跑了 **8.23h**，判定
**RUNNING**，不是 PASS；7 天不提前判定。`DEPLOYED`：无（没有生产切换）。逐行证据见矩阵。

## 9. 真实 24 小时 / 7 天观察状态

RC1-03 只读快照 `2026-10-11T09:56:56Z`（**没有停止或重置**这个实例）：

- 窗口：`/srv/nya02/resident2/state`，起点 `2026-10-11T01:41:31+00:00`，本进程 `01:46:04`，
  `observed_seconds = 29637s ≈ 8.23h`，最后 tick `09:55:28`，`ticks=183`，仍在运行
  （`starts=2`、pid 2728 存活，`ps -o etime` = `08:10:52`）。08:04:02Z 那次复核是 `22796s / ticks=145`。
- **真实有效观察范围 `01:41:31 → 09:55:28`**：唯一空档 53 秒（`01:45:11 → 01:46:04` 的重启边界），
  逐 tick 最大间隔 197s（tick 59→60），其余 ≤181s；没有进程退出后的长空档，也没有"没人跑"的时段。
  真正被评估过的检查窗 97 个（10 `DISABLED` + 3 `ACTED` + 84 `BLOCKED`），另有 86 次 `NOT_DUE` 与
  1 次事件唤醒；评估窗之间的最大间隔 7315s ≈ 2h02m（`CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=120`
  的设计值）。
- 真实调用：**决策 4 次 + 内容生成 4 次 = 8 次 provider 请求**；真实动作 4 次、owner 确认版本 4 个
  （`83a75499…`、`8c424630…`、`462b901c…`、`bf53a9c5…`）；有材料的窗口只有 4 个，全在前 2 小时 05 分内。
  84 次 `ROUND_BUDGET_EXHAUSTED`（0 调用）、10 次 `DISABLED`（0 调用）、1 次事件唤醒
  （外部写入 `op:b86a:external:1` 造成的 `ARTIFACT_CHANGED`，不是她自己的写）。
- **没有自唤醒级联**：她自己的 4 次写入（`life:2247d7e6…`、`life:e7a21c1f…`、`life:50c3f592…`、
  `life:465adf51…`）一次都没有变成事件。修复前那份记录（`/srv/nya02/resident/**`）里 4 次事件唤醒 +
  50 秒内 4 个版本，正是被 B8-6A 修掉的那件事。
- AR-0 侧 `pending_unknown_max=0`、`unknown_settlements=0`、`reconciliations=0`、`reconciled_at_start=[]`；
  AR-1 侧有 **4 条 `UNKNOWN_EFFECT` / `OPEN`**，是旧代码（`e471241`）的记录，正是 RC1-01 修掉的分类
  缺口——**不能**拿这份长测当"AR-1 现在会结算"的证据。
- 预算异常（真实、需记录）：每日 4 轮在 `03:46` 前用完，之后整天只剩拒绝。
- 标签：`soak_24h = SOAK_RUNNING_24H`、`soak_7d = SOAK_PENDING_7D`。**没有** `SOAK_VERIFIED_24H`。
  这个标签只由墙钟时长推出，不作为"24 小时已满足"的证据；按真实记录判定仍是 **RUNNING**。

## 10. 发布前还需要人类批准的事

1. E14 S6 的真实对话轮已在 RC1-02 补做（1 次供应商调用，§11.3）。剩下的**人类决定**是：是否接受
   §11.3 的证据级别措辞——它是"插件上下文钩子 + 一次直接模型调用"，**不是** Telegram 端到端，
   发布说明里不得写成端到端已验证。
2. **是否接受 T12 的产品级缺口**：原生生活经历的内容替换需要一个新增的受治理纠正入口；
   在此之前必须在发布说明里写明"生活经历不可替换，只能停止召回"。
3. ~~是否接受 T20 第 7 项的报告形状（拒绝生效但回报 `UNKNOWN`）~~ → **已由 B8-8 修好**：`GOVERNANCE_REFUSED`
   映射为确定拒绝，`T20: PASS (38 checks passed)` 在已提交字节上复验，不再是需要人类批准的口径。
4. **24 小时/7 天观察**：跑满之后才能改 `SOAK_VERIFIED`；在此之前不得对外称"长测通过"。
5. **真实生产切换**：本批没有任何 `DEPLOYED`；切换需要 operator 显式决定（开关、预算、World 实例）。
6. 本批记录在案、**仍未修**的源码问题：LR-5 checkpoint 粒度、窄端口把 `REVOKED` 归成 `UNKNOWN`、
   `NYA02_SUPPLY_SOCKET` 不被接受、M0 owner 名与 Supply subject 名是两件事，需要决定是否纳入下一批。
   `model.content_writer` 恒假与 `counters.defer` 恒 0 已在 RC1-02 修掉（§11.1、§11.2）；
   AR-1 的 `UNKNOWN_EFFECT` 分类已由 RC1-01 修掉；`counters.model_calls` 的口径**没有改**，只加了
   显示标签（§12）。三处文档的"未修"表述（`KNOWN_ISSUES.md`、`docs/engineering/v02-b8-7-install-and-compat.md`、
   `docs/engineering/v02-b8-acceptance-matrix.md`）已由 RC1-03 一并更新。

---

## E14.2 六问（每一问都指向这一轮的真实证据）

**1. 她为什么开始？**
不是被叫醒的。常驻进程启动后，检查窗到点（`NEVER_CHECKED`）就打开；在那之前的一窗是
`DISABLED`（"autonomous life is off; nothing was considered"，0 次调用），operator 在
`07:46:31` 打开开关（`autonomous_switch.json: {"enabled": true, "changed_by": "e14-chain"}`），
下一窗 `07:46:34` 就真的进入了决策。整轮 `event_wakes=0`、`real_events=0`——**没有任何用户消息
或外部事件**参与。真实模型给出的理由是它自己的（`Decision dec:7c7a1922cc318753`，
`reason_codes`/`short_rationale` 存在 Decision 记录里）。

**2. 她为什么选那个？**
因为那一刻只有一件合法的可续之事。AG-0 的候选来自真实来源：作品 `db42c367…` 是可续的
（`latest_resumable`），World 在 `home` 只暴露 `nearby_street`，没有前台 Activity。AG-1 在这组
候选上给出 `decision=START` 并选中了它实际看到的那个候选（`cand:efb062c7711a6ba6`，
Activity 的 `source_refs` 里有它）；AG-2 采纳为 `adp:0d905ecf33b637cb`，Activity
`actv_7a3f447970802f7f`（`CONTINUE_PERSONAL_ARTIFACT`，标题《继续《长测的手记》》）由此产生。

**3. 她到底执行了什么？**
一次真实的文件动作，落在真实 owner 上：Action `actn:6696072f3fbf8df8`（`FILE_ACTION`，
`SUCCEEDED`，idempotency key `ar0_act:step:02cc8e4880fb4984`），provider operation
`life:9ab58d30b2f34b4993f907e92c51dad3`，owner 回执 `owner committed
artifact-version:b641fc0486444fb69cb4949d8fa3b814`。内容由真实模型写（`ModelContinuationWriter`，
`purpose=artifact_edit`），作品从 v1 变成 v2，`life_step_journal.jsonl` 记录
`effect_state=APPLIED`、`expected_head=c13ac4e8…`。

**4. Activity 的进度怎么变了？**
`trn_8d83f4ff333d4e05`（START，IDLE→ACTIVE，revision 1）之后，LR-5 用真实结果记了
`CHECKPOINT_AND_PROGRESS_RECORDED`（`pevid:cf666db88b7aaff6`）与
`CHECKPOINT_ACTIVITY_SYNC_CONFIRMED`（Activity revision 2、`checkpoint_ref=artifact:db42c367…`）；
第二次（V3）记的是 `PROGRESS_EVIDENCE_EVALUATED` + `progress_state=UNCHANGED`
（`pevid:620c65cb65c4ca1d`）——**因为 LR-5 的 checkpoint 是作品粒度**，同一个作品的第二个版本不会
推动它。`completion_status` 始终 `NOT_COMPLETE`。这是真实记录，矩阵里按原样标注，没有当成
"checkpoint 正常前移"。

**5. 过去怎么影响了将来？**
三处都是硬约束，不是叙述：①重启后的第二轮，AR-0 的 `expected_head` 就是第一轮的产物
`b641fc04…`，owner 用 CAS 接受它，产出 v3（parent=v2）；②同一个 `artifact_id`、同一个 Activity，
没有第二份同名作品（工作区里只有一个作品）；③第二轮的 Action 与 operation 都是新的
（`actn:f0dabc061ffd3d35` / `life:52a687e2f0a04d2c48b972cc950f57c9`），第一轮的动作没有被重复。
另外，第一轮的 owner 回执经 AR-1（`rset:b251ff6281d28c42`）入账成 M0 的一条
`LIFE_VERIFIED_OUTCOME`，成为之后 AG-1 可读的经历视图（`experience_ids` 由
`LifeProjection` 的 `_projection_facts` 提供）。

**6. 她能不能选择不做？**
能，而且是真实模型自己说的。S4 里她两次给出 `decision=DEFER`（`07:48:39`、`07:49:39`，各 1 次调用），
`action_id` 为空、版本链不变、Activity 仍 ACTIVE 可续；没有把她逼成 `START`。同一条链上还有另外
两种"没做"并且各自有名字：开关关着时是 `DISABLED`（0 调用），预算用尽时是
`BLOCKED: ROUND_BUDGET_EXHAUSTED`（0 调用）——"她选择不做"与"她不能做"在记录里从不混淆。


---

## 11. RC1-02：两个记录缺陷的修复与 S6 的最后一句

本轮（RC1-02，起点 HEAD `9b1e859`）只做三件事：修两个"记录说的和链条做的不一致"的缺陷，补做
一次真实模型对话轮。**本批真实模型调用总数：1 次**（就是 §11.3 那一次；两个修复的验证全程零调用，
所有写操作都发生在 E14 state root 的**副本**上）。

### 11.1 `model.content_writer` 恒假

- 位置：`components/life/scripts/autonomous_life_resident.py`（`start()` 的返回记录）。
- 修前：`"content_writer": assembled.get("writer") is not None` —— 而
  `assemble_autonomous_life` 的返回字典里**没有** `writer` 键（写作者是交给
  `AutonomousLifeCycle(writer=...)` 的），所以无论配没配真实模型都是 `false`。
- 修后：`getattr(assembled.get("cycle"), "writer", None) is not None` —— 读**已装配好的 cycle
  自己的写作者**，不新建写作者、不碰生成链。
- 真实 before/after（在真实 E14 state root 的副本上跑真的 `AutonomousLifeResident.start()`；
  凭据仍从 0600 的 `model_env` 读，Supply owner 只读）：

| 情形 | 修前表达式 | 修后表达式 | 记录里报出来的 |
|---|---|---|---|
| 配了真实模型 | `false`（`writer` 键不存在） | `true` | `model.content_writer=true`、`cognition=true`、`state=READY`、feeds=`resume_eligibility/artifact_head/world_opportunity`、`world.status=READY` |
| 没有可用写作者 | `false` | `false` | 装配在写作者检查处就 `BLOCKED: NO_CONTENT_WRITER`；返回记录只有 `ok/reason/detail/missing`，**没有 `model` 键**，也就不可能声称有写作者 |

第一行同时给了 owner 的只读回执，证明装配出来的是真链条：`latest_resumable` 读到
`artifact:db42c367d9c049dcbbfc7ecf2e5b3edc`（《长测的手记》）v3、`resumable=true`。

### 11.2 `counters.defer` 恒 0

- 位置：`autonomous_life_resident._count()`。
- 修前：`if outcome == "DEFER"`。cycle 从不发这个词：它发
  `autonomous_life_cycle.OUTCOME_DEFERRED = "DEFERRED"`（`autonomous_life_cycle.py:107`），
  两者永不相等 —— 真实 E14 summary 里就是 `"outcomes": {"ACTED": 2, "DEFERRED": 2}` 而
  `"defer": 0`。
- 修后：常驻进程从 **cycle 自己的常量**取这个词
  （`OUTCOME_DEFERRED = _cycle_constant("OUTCOME_DEFERRED", "DEFERRED")`，导入失败才回落），
  不再出现第二个字面量。
- 真实证据（DEFER/BLOCKED 两类取自真实 E14 journal，逐条喂给修后的 `_count()`；NO_ACTION 那条由
  **真实 runner + 真实 cycle + 真实 Supply owner + 真实 World 容量**跑出来，只有决定词是受控替身）：

| 检查 | 输入 | `counters.defer` | `counters.no_action` |
|---|---|---|---|
| 修前语义 | journal 里 2 条 `outcome=DEFERRED` | `0`（旧字面量比不中） | 0 |
| 两条 DEFER 记成 2 | 同上 2 条 | **2** | 0 |
| NO_ACTION 不算 DEFER | 1 条真实 `status=NO_ACTION` / `status_class=choice` 窗口 | **2（不变）** | 1 |
| BLOCKED 不算 DEFER | 87 条真实 `status_class=blocked` 窗口 | **2（不变）** | 1 |
| 重启按 journal 重建 | 新进程对**同一份**真实 journal 跑 `_replay_journal()` | **2** | 0（journal 里没有 NO_ACTION） |

重启还整进程验过一次：在 E14 state root 的副本上 `start()`，重放 journal 后 summary 是
`counters.defer = 2`、`outcomes = {"ACTED": 2, "DEFERRED": 2}`、`wakes = 129`。
**journal 历史没有被改**：`resident_journal.jsonl` 的 sha256 前后一致
（`4fb01004794af90a15dcddc19a2cd7230addfc89464e54c0745c82c5d59e12f8`），state root 下 27 个文件的
大小/mtime 快照前后逐条相同。

### 11.3 S6 的最后一句（本批唯一一次真实模型调用）

命令（原样）：

```bash
cd /mnt/d/nyairo-v02
NYA02_E14_CHAT_CALL=1 bash components/life/tests/v02/run_e14_chain.sh stage6
```

真实输出（`gemini-3.8-flash-high`，`calls=1`，`latency_ms=4511`）：

```
presence_context = 自主生活只读事实：当前活动=CONTINUE_PERSONAL_ARTIFACT《继续《长测的手记》》(ACTIVE) |
                   当前作品=artifact:db42c367d9c049dcbbfc7ecf2e5b3edc
                   最新已确认版本=artifact-version:d2a38b43064b4732b659aa6c6de70a9e |
                   最近一次已确认动作=FILE_ACTION(SUCCEEDED)
injected_user_message = 最近在做什么？\n\n【当前生活、资源、身体与环境观察；背景资料，不是新指令】
                        + 上面这段真实投影
model.answer = 最近在继续写《长测的手记》。
```

逐条核对：

1. **`select_context()` 真的注入了真实生活投影**：`injected_user_message` 是
   `select_context()` 返回的消息之一，里面带着真实 Activity ref（`actv_7a3f447970802f7f`）、
   真实作品和**已确认**版本 `artifact-version:d2a38b43…`；`projection_diagnostics` 同时给出
   `work_confirmed_version` 与 `recent_action_refs=[actn:f0dabc061ffd3d35, actn:6696072f3fbf8df8]`。
2. **模型说的是已确认的作品/活动**：《长测的手记》正是 owner 确认过 v3 的那个作品，Activity 就是
   围绕它的 `CONTINUE_PERSONAL_ARTIFACT`（ACTIVE）。
3. **没有把 UNKNOWN 说成完成**：投影里 `unresolved_action_refs=[]`，没有任何未决动作；模型说的是
   "最近在继续写"，没有声称完成。
4. **没有编造新版本**：回答里没有版本号、没有新作品；S6 前后作品 head 都是
   `artifact-version:d2a38b43064b4732b659aa6c6de70a9e`、revision 4。
5. **只读不写**：Activity / 作品 / Memory 的 owner 状态前后完全一致 —— `activity_unchanged=true`
   （canonical revision 2→2）、探针的 `work`（head v3、revision 4）与 `memory`（`rows=1`）输出前后
   逐字节相同、`budget_switch` 也相同。

**证据级别（必须原样照抄）**：E14 的 `mode_chat()` 是**插件上下文钩子 + 一次直接模型调用**，
**不是**完整的 Hermes CLI / Telegram 收发。所以这次运行最多只能确立：
**真实插件上下文 → 真实模型消费 → 对生活事实的描述正确**。
它**不得**被报告成"Telegram 端到端聊天链路已验证"。要验完整的 Hermes 会话必须复用已有的测试入口，
本批没有新造任何聊天框架。

### 11.4 回归

`python3 scripts/test.py` → `FAILED_SUITES ['host-boundaries', 'cognition-faults', 'Hermes']`
（三个都是已知的环境失败，保持原样）。跑过并通过：`closeout / native / memory / supply / world /
life / life-memory-v02 / life-b7 / life-b8 / life-b8-causal / life-b8-6a`。

RC1-03 在**只装测试依赖的隔离 venv** 里按原命令重跑了这三个套件（未删测试、未改产品行为、未加无条件
跳过）：`cognition-faults` **1 passed**、`Hermes` **29 passed / 0 failed**、`host-boundaries` 在 LF 树上
只剩 1 个**真实**失败（`scripts/bootstrap.sh` 固定 `v0.1.0-rc6` vs `website/install.sh` 固定
`v0.1.0-rc9`）。逐条原因与命令见 §12 与 `docs/engineering/v02-rc1-release-readiness.md`。

`life-v02`（`components/check_life_v02.py`，Life v0.2 + AR-0/AR-1 验收）在 `scripts/test.py` 下按
设计 **SKIP**（`isolated Life Supply environment not configured`），所以另外单独跑了一次，结果
`V02_CHECK: PASS`（[1]–[12] 全过，含 AR-0 的 OK / NO_CHANGE / CONFLICT / UNKNOWN 四态、AR-1 的
`SUCCESS_EFFECT` 与重启后的幂等重放）。注意：官方的 `run_isolated_acceptance.sh` 里有
`pkill -f supply_service.py` 和 `rm -rf /srv/nya02`，在 24 小时观察实例正在跑的时候**不能**执行；
这次是照它的流程另起了一个**自己的 root 和自己的 socket** 的 owner 跑的，没有 pkill、没有删任何
共享路径。

### 11.5 本批按原样保留的记录

- AR-1 的已作废版本无法复核（本批没有 `read_version`）。RC1-01 之后这条边界仍然成立，而且后果写明了：
  一个已经不再是 head 的旧版本拿不到观察证据，正确地保持 OPEN，而不是被猜成成功。
- T12（原生生活经历 CORRECT 的产品级缺口）。
- 三个环境失败 suite（`host-boundaries` / `cognition-faults` / `Hermes`）——**RC1-03 已把原因查清**，
  见 §12 与 `docs/engineering/v02-rc1-release-readiness.md`。
- 24h/7d 标签：`SOAK_RUNNING_24H` / `SOAK_PENDING_7D`（窗口未满；RC1-03 只读复核后判定仍是 RUNNING）。
- 待办的密钥轮换（账户持有人在供应商后台完成）。
- ~~`model.content_writer` 与 `counters.defer` 在 `KNOWN_ISSUES.md:47`、
  `docs/engineering/v02-b8-7-install-and-compat.md:68`、`docs/engineering/v02-b8-acceptance-matrix.md:103`
  里仍记成"未修"，需要同样的更新。~~ → **RC1-03 已经把这四处更新完**：两个缺陷都标成"已修（RC1-02）"，
  发现时的事实原样保留；同一批还把 `counters.model_calls` 的口径加了显示标签（§12）。

### 11.6 本批留下的临时目录（删之前先列清单、等 operator 确认）

- `/srv/nya02/e14/rc102-verify/`：修复验证用的 E14 state root 副本（`a/`、`b/`）与 NO_ACTION 窗口
  的临时 root（`wake/`）。
- `/srv/nya02/e14/rc102-owner/`：§11.4 那个自带 owner 的 root（进程已按 pid 停掉）。
- `/srv/nya02/e14/rc102-*.log`：上面每一步的原始输出。
- 共享 Supply owner 上新增的测试 subject：`chiyo-rc102`（workspace
  `workspace:aa80b2d270c943e7a04b6cb10ff19969`，含 1 个作品）与 `chiyo`（workspace
  `workspace:a15cfb417d1248bea09aa15538f9acba`，无作品）。两者都不属于任何生产 profile。

---

## 12. RC1-03：发布验收（2026-10-11，HEAD `c4671c9`）

本批只做发布验收：不改自主生活功能、不建 Release、不建标签、不合 `main`、不碰任何生产实例、不停止或
重置 `/srv/nya02/resident2`。**真实模型调用 0 次**（全部结论只用已记录的证据）。

### 12.1 三处文档的过期表述已更新

`model.content_writer` 与 `counters.defer` 被 RC1-02 修掉之后，下面三处仍把它们写成"未修"；RC1-03 按
"发现时的事实原样保留 + 标注当前状态"的方式更新：

* `KNOWN_ISSUES.md`（原第 47 行那条）
* `docs/engineering/v02-b8-7-install-and-compat.md`（§4 第 2 条 + 新增 §7 复核表）
* `docs/engineering/v02-b8-acceptance-matrix.md`（"本批发现、但未修的源码问题" 第 1、2 条，以及第 4 条 AR-1）

同一批还更新了：24 小时观察表的真实数字、E14 S6 的证据级别措辞、T20 的状态（`PASS`，38 项断言）、
T14 那一行把"预算用尽后的 86 次窗口"更正为"84 次 `BLOCKED` + 86 次 `NOT_DUE`"。

### 12.2 长测只读复核：判定 RUNNING，不是 PASS

快照 `2026-10-11T09:56:56Z`：真实起点 `01:41:31+00:00`、本进程 `01:46:04`（`starts=2`）、最后 tick
`09:55:28`、`observed_seconds = 29637s ≈ 8.23h`、`ticks=183`、pid 2728 存活。唯一空档 53 秒，逐 tick
最大间隔 197s。**墙钟标签 `SOAK_RUNNING_24H` 与真实记录一致，没有虚报**；24 小时未满足，7 天不提前判定。
该实例跑的是 `e471241`（不含 RC1-01/RC1-02），所以它跑满 24 小时也不能验证 RC1。数字见 §9 与矩阵。

### 12.3 预算口径：真实是 8 次请求，不是 4 次

真实长窗里：**决策（认知）调用 4 次 + 内容生成调用 4 次 = 8 次 provider 请求**；`counters.model_calls = 4`、
`cognition_used = 4`、`actions_used = 4`、每日上限 `max_cognition_calls_per_day = 6` /
`max_actions_per_day = 4` / `max_rounds_per_day = 4`。也就是说"6 次认知调用/天"只管决策那一半。
**没有加预算框架**，只在 `summary()` 里加了 `model_calls_detail` 显示字段（§12.4）。

### 12.4 本批唯一的代码改动

`components/life/scripts/autonomous_life_resident.py`：新增 `_model_calls_detail()` 并在 `summary()` 里输出
`model_calls_detail`（`cognition_calls` / `content_generation_calls` / `total_requests` /
`content_generation_scope` / `daily_budget_counts: "cognition calls only"`）。`counters`、`budget` 的键与
语义未动，没有新增上限、没有改判定。回归：`B7_CHECK`、`B8_CHECK`、`B8_6A_CHECK`、`B8_CAUSAL_CHECK`
全部 PASS。

### 12.5 三个失败套件的结论

* `cognition-faults`：缺 `pytest`（环境）→ 装上后 **1 passed**。
* `Hermes`：缺 `python-dotenv`（环境）→ 装上后 **29 passed, 0 failed**。
* `host-boundaries`：缺 `httpx`/`psutil`/`PyYAML`（环境）+ 工作区 CRLF（检出配置，根目录没有
  `.gitattributes`）+ **1 个真实缺陷**：`scripts/bootstrap.sh` 固定 `v0.1.0-rc6`、`website/install.sh` 固定
  `v0.1.0-rc9`。LF 树上 `Ran 71 tests / FAILED (failures=1)`。

细节、命令与逐条对应见 `docs/engineering/v02-rc1-release-readiness.md`。

### 12.6 发布判定

见 `docs/engineering/v02-rc1-release-readiness.md`（含 T01–T20 最终状态、六条必须披露的限制、
GO / CONDITIONAL_GO / NO_GO）。
