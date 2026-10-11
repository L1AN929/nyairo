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
| AR-1 结算 | `result_settlement_ar1.py` | Settlement（本轮两次都是 `UNKNOWN_EFFECT`/`OPEN`，见矩阵 §本批发现） |
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
- T06/T07 的**完整**暂停/放弃生命周期（真实 LR-2 transition）。
- B8-7 的在线撤回链（`ACTIVE grant → REVOKED → 新写被拒 → head 不变`）只读复核成立。
- 24 小时观察窗仍在真实运行（§9）。

**未完成 / 不得声称**
- 真实模型的那一轮对话（E14 S6 的最后一句），因为调用上限已用完（§8）。
- 24 小时与 7 天的 `SOAK_VERIFIED`：窗口未满。
- T12 的原生生活经历 CORRECT（产品级缺口）。
- T20 第 7 项：拒绝生效但报告为 `UNKNOWN`，保留为实际失败。
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
# 允许 S6 再花 1 次供应商调用（默认 0：上限已经用完）
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

## 8. T01–T20 的最终状态

`PASS`：T01 `E2E_VERIFIED`、T02 `E2E_VERIFIED`、T03 `E2E_VERIFIED`、T04 `TESTED_ISOLATED`、
T05 `TESTED_ISOLATED`、T06 `TESTED_ISOLATED`、T07 `TESTED_ISOLATED`、T08/T09/T10/T11
`TESTED_ISOLATED`、T13 `TESTED_ISOLATED`、T14（机制）、T15 `TESTED_ISOLATED`、T16 `E2E_VERIFIED`、
T17 `TESTED_ISOLATED`、T18 `E2E_VERIFIED`、T19 `TESTED_ISOLATED`。
`DEFERRED`：T12。`PASS(7/8) 保留实际失败`：T20。
`SOAK_VERIFIED`：无（24h 未满）。`DEPLOYED`：无（没有生产切换）。逐行证据见矩阵。

## 9. 真实 24 小时 / 7 天观察状态

- 窗口：`/srv/nya02/resident2/state`，起点 `2026-10-11T01:41:31+00:00`，本进程 `01:46:04`，
  复核时刻（`2026-10-11T08:04:02Z`）`observed_seconds = 22796s ≈ 6.33h`，仍在运行
  （`starts=2`、`ticks=145`）。
- 真实调用 4 次、真实动作 4 次、owner 确认版本 4 个；有材料的窗口只有 4 个，全在前 2 小时 05 分内。
- 86 次 `NOT_DUE`（0 调用）、46 次 `ROUND_BUDGET_EXHAUSTED`（0 调用）、1 次事件唤醒
  （外部写入 `op:b86a:external:1` 造成的 `ARTIFACT_CHANGED`，不是她自己的写）。
- **没有自唤醒级联**：唤醒间隔是 `180s × 113` 与启动期 `10s × 16`，没有任何一次由她自己的写触发。
  修复前那份记录（`/srv/nya02/resident/**`）里 4 次事件唤醒 + 50 秒内 4 个版本，正是被修掉的那件事。
- `pending_unknown_max=0`、`unknown_settlements=0`、`reconciliations=0`；重启恢复正常。
- 预算异常（真实、需记录）：每日 4 轮在 `03:46` 前用完，之后整天只剩拒绝。
- 标签：`soak_24h = SOAK_RUNNING_24H`、`soak_7d = SOAK_PENDING_7D`。**没有** `SOAK_VERIFIED_24H`。

## 10. 发布前还需要人类批准的事

1. **再花 1 次供应商调用**完成 E14 S6 的真实对话轮（命令见 §4，`NYA02_E14_CHAT_CALL=1`）。
2. **是否接受 T12 的产品级缺口**：原生生活经历的内容替换需要一个新增的受治理纠正入口；
   在此之前必须在发布说明里写明"生活经历不可替换，只能停止召回"。
3. **是否接受 T20 第 7 项的报告形状**（拒绝生效但回报 `UNKNOWN`）。
4. **24 小时/7 天观察**：跑满之后才能改 `SOAK_VERIFIED`；在此之前不得对外称"长测通过"。
5. **真实生产切换**：本批没有任何 `DEPLOYED`；切换需要 operator 显式决定（开关、预算、World 实例）。
6. 本批记录在案、**未修**的四个源码问题（`model.content_writer` 恒假、`counters.defer` 恒 0、
   LR-5 checkpoint 粒度、AR-1 `UNKNOWN_EFFECT` 分类）需要决定是否纳入下一批。

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
