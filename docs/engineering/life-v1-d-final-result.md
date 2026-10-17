# D-final 回执：整版收尾的两处增量（真实跨进程重启、短期评价消费者）

- 候选树 `D:\nyairo-v03-backend`｜branch `work/v03-w01-navigation`｜HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（均未移动）
- **全部 UNCOMMITTED**：未 `stage`/`commit`/`reset`/`clean`/`checkout`，未删除/迁移任何文件（含 pyc/socket/log/目录），
  未整仓复制，未改 provider/profile/凭据，未 `py_compile`；所有 python 调用带 `PYTHONDONTWRITEBYTECODE=1`
- 旧回执 `life-v1-d-result.md`、`life-v1-d-r1-result.md`、`life-v1-d-r1b-result.md` **保持原文**；本文件只记最后增量
- 本轮只跑了两条新定向测试 + 两条笔记门禁；旧两专项/55/17/86/36/9 inherited/continuous 大套**未重跑**（父级已核）

---

## 1. 增一：真实跨进程重启（原单第 6 条，不扩大）

**新文件** `tests/test_life_v1_restart.py`（`RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`）。

**为什么不是 probe**：旧证据是"另一个 PID 只读读盘"（`DRestartAndQuietTest`、drill 第 8 段），只能证明文件读得到；
D 首轮又是同进程重建 runtime（PID 没换）。本轮改成两个**独立 worker 子进程各自装配真实 runtime 并各跑一轮**，
而且**父进程刻意不建 runtime**（否则 canonical lease 一直在父进程手里，第二个真实装配根本起不来）。

| 阶段 | 实际做了什么 | 关键断言 |
|---|---|---|
| worker A（PID A） | 从 `odl.instance_environment` 装配真实 `ProductionLifeRuntime`（真实 Supply/World/Activity/Memory 边界、AR-0 适配器、真实 `MemoryAdmissionExperienceConsumer`）+ `assemble_autonomous_life`（实例自己的 `LifeRequestBudget`），真跑一步（owner receipt + 新版本），`budget.reserve("decision")` 记一次请求，`odl.set_pause(True)`，然后 `runtime.close()` **释放 lease** | `assembled_state=READY`、`outcome=ACTED`、`owner_receipt=true`、`owner_version_created=true`、head 前进、`pause.paused=true`、`utc_day_calls = 起始+1`、`closed=true`（lease 不再被持有） |
| 空档期 | 父进程**不建 runtime、不跑轮**，只读 | head 与 M0 行数在两次 worker 之间不变（无离线经历） |
| worker B（PID B） | 同 data root，**新** runtime、**新** assembly、**新**预算对象，先以暂停态跑一轮，再 resume 并续同一 Activity | `state_class=PAUSED`、暂停轮 `outcome=PAUSED_BY_OPERATOR`/`llm_calls=0`/决策分支调用 0 次/head 未变；resume 后 `outcome=ACTED`、`activity_ref` 与 A 相同、owner 真出新版本；`run_id` 相同、`utc_day_calls`/`utc_day_max`/`run_window_max` 延续不重置；书签仍在；`closed=true` |

现场报告（真实日志 `/tmp/nyaw05r.jdK3q/d-r1-recovery-r1c-restart-final.log`）里可见：worker B 起始
`utc_day_calls=1`（A 写下的那次）、`pause.paused=true`、`head_at_start == head_while_paused`、resume 后
`head_after_round` 变化、`activity_after.status=ACTIVE`、`budget_after_reserve.utc_day_calls=2`。
worker 不是 resident 循环，所以入口如实报 `STOPPED`（不虚构 `IDLE`）——这一点也写进了断言与注释。

## 2. 增二：短期评价的最小消费者（A 输出 → D 实际输入，不扩 S07）

| 接点（当前行号） | 内容 |
|---|---|
| `autonomous_life_cycle.py` `__init__`（`appraisal_source`）+ `:1058` 起 | `read_appraisal()` 只转发 A 的 `ShortAppraisalView.build()` 视图（失败记 `read_error`）；`appraisal_context()` **只取 `status == VALID`** 的项，每项带 `appraisal_ref`/`source_refs`/`observed_at`/`valid_until`/`declared_scope`/`uncertainty`，并显式带 `authority: None`、`candidate: false`、`stable_fact: false`、`tone_inference_used: false`；`consideration_context()` = admitted experiences + 上面这些项 |
| `evaluate_once`（`prior_experiences=consideration`、`base["appraisal"]=…`） | 模型的**同一个**有界上下文口（`prior_experiences`）真的带上评价；轮次记录 `appraisal`（`has_valid_appraisal`/`valid_appraisal_refs`/`invalid_appraisals`/`empty_reason_code`/`source_revisions`） |
| `materials()` / `status()` | 只报 `appraisal_wired` 与最近一次读的 compact 结果，不参与"有没有材料"，不作为权限 |
| `autonomous_life_assembly.assemble_autonomous_life(appraisal_source=…)` | 透传 + 返回 `appraisal_wired` |
| `autonomous_life_resident.start()` | 树里没有 S07 owner → 传 `None`，如实报 `appraisal_wired:false`（显式实验输入只在测试里用） |

**没有**新增 store/文件格式/authority；**没有**新 Candidate 通道；没改 AG-1 请求 schema（`prior_experiences`
本来就是"模型可查阅的有界上下文"，用 `kind="short_appraisal"` 区分）。

**证据** `tests/test_life_v1_appraisal_context.py`（3 项，按 id 跑）：
有效项真的出现在 AG-1 收到的 `request.prior_experiences` 里（带来源、`valid_until`、scope）；
一条带 `correction_ref`+`corrects_refs` 的合法纠正 → 上下文为空、`empty_reason_code=APPRAISAL_CORRECTED`；
`now >= valid_until` → `APPRAISAL_EXPIRED`；来源丢失 → `APPRAISAL_SOURCE_LOST`；
无输入 → `NO_APPRAISAL_INPUT`；安静窗口（`act=False`、无 port、无观察）仍 `NO_MATERIAL`、`llm_calls=0`、
决策分支 0 次（**空评价不开一轮**）；候选串里**没有** `appraisal` 字样；视图自报 `candidates_emitted=0`、
`actions_authorized=0`、`writes_performed=0`。

## 3. 本轮实际命令 / 真实 exit / 耗时

| 命令 | exit | 结果 |
|---|---|---|
| `run_tests.py r1c-restart-final tests.test_life_v1_restart.RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity` | **0** | `Ran 1 test in 13.359s` / `OK` |
| `run_tests.py r1c-appraisal-final …AppraisalContextTest.{valid_sourced, correction_or_expiry, empty_costs_no_call}` | **0** | `Ran 3 tests in 21.319s` / `OK` |
| `node --experimental-strip-types …\scripts\verify-agent-note-tree.ts`（repo cwd） | **0** | `ok: 7 note(s) tree and relative links verified` |
| `node --experimental-strip-types …\scripts\verify-agent-note-format.ts`（repo cwd） | **0** | `ok: 7 note(s) verified` |

日志（保留未清理）：`/tmp/nyaw05r.jdK3q/d-r1-recovery-r1c-restart{,-final}.log`、
`…-r1c-appraisal{,-final}.log`；先前 drill*/r1b* 日志全部保留。未重跑：36 C / 9 inherited / 55 / 17 / 86 /
全仓 / CLI / `w05rr1c_neg.sh` / continuous drill。

## 4. 本轮自有 diff（其余 UNCOMMITTED 是既有）

| 文件 | 状态 | 改动 |
|---|---|---|
| `components/life/scripts/autonomous_life_cycle.py` | 改 | `appraisal_source` 入参 + `read_appraisal()`/`appraisal_context()`/`consideration_context()`/`_compact_appraisal()`；`evaluate_once` 用 `consideration` 作 `prior_experiences` 并记 `base["appraisal"]`；`materials()`/`status()` 报 `appraisal_wired`/`last_appraisal`；`autonomous_life_cycle_from_env(appraisal_source=…)` |
| `components/life/scripts/autonomous_life_assembly.py` | 改 | `assemble_autonomous_life(appraisal_source=…)` 透传 + 返回 `appraisal_wired` |
| `components/life/scripts/autonomous_life_resident.py` | 改 | `start()` 传 `appraisal_source=None`（无 S07 owner，如实报） |
| `tests/test_life_v1_restart.py` | **新增** | 两个独立 worker 的真实跨进程重启 harness |
| `tests/test_life_v1_appraisal_context.py` | **新增** | 短期评价消费者 3 项定向测试 |
| `.agents/notes/implemented/testing/2026-10-17-cross-process-life-restart-harness.md` | **新增** | 重启证据的决策笔记 |
| `.agents/notes/implemented/architecture/2026-10-17-short-appraisal-bounded-context.md` | **新增** | 短期评价消费方式的决策笔记 |
| `.agents/notes/implemented/feature/2026-10-16-continuous-life-prototype.md` | 就地改 1 行 | Decision #3 链到新的重启 harness（他人笔记未动） |
| `docs/engineering/life-v1-runbook.md` / `life-v1-capability-status.md` | 改 | 见 §5 |

## 5. 文档同步（含父级指出的那句）

- `life-v1-runbook.md` §8：把"`DEFER` 不推进任何一项"改准 —— **`status`/`read` 才是不推进任何一项**；
  `DEFER`/`WAIT`/`PAUSE`/`NO_ACTION` 是已提交 Decision，会推进 `seen` 与 `since`，**只有**被 owner 消费的 RESUME 推进
  `adopted`；被拒/未 `APPLIED` 的 adoption 不推进 `adopted`。同节新增：restart 证据两层（只读 probe vs 两进程真实重启）、
  短期评价消费语义、以及两条新测试的复跑命令（并注明"按 id 跑，否则会连带收集基类继承的 W05 三条"）。
- `life-v1-capability-status.md`：`短期评价` 行由"部分/无消费者"改为"已接（消费者）/生产无源"并给证据；
  统一入口行改为"真实跨进程重启已证"（旧的只读 probe 保留为历史）；文末新增 `D-final` 段（重启/评价/仍未验）。

## 6. 已证 / 未验（不夸称）

**已证（本轮新增，真实 exit 0）**：两进程真实重启（新 runtime/新装配/新预算，pause 态 0 调用，resume 续同一 Activity
出真实新版本，run identity/计数器/上限/书签接续，空档期无写入）；短期评价作为有界上下文被真实消费，纠正/过期/丢失不消费，
空评价 0 调用、不成 Candidate、不产生 authority。

**仍未验（不得替代）**：真实产品模型行为（决策/内容仍是确定性替身）；24h / 真实时钟短跑；完整 P2；真并发
（仍 `DEFERRED_BEYOND_CURRENT_CONTRACT`）；生产 World 包是否声明家具；S07 affect owner（评价生产侧恒空，只有显式实验输入）；
驻留进程整体重启（worker 是一次 `evaluate_once`，入口状态如实 `STOPPED`）。

## 7. 一处他人笔记的事实不一致（不顺手改，只登记）

`.agents/notes/implemented/architecture/2026-10-16-non-coercive-life-actions.md` 写到"Resolver 支持进食"。
当前代码里 Resolver 的动作集是 `world_action_resolver.py:166 ACTIONS = ("MOVE", "PICK_UP", "PLACE", "POSE")`，
身体后果集是 `body_consequence.py:48 SUPPORTED_ACTIONS = ("MOVE", "POSE", "REST", "PICK_UP", "PLACE")` —— **都没有进食**。
该笔记不是本任务创建，未改动；请父级外部登记。本轮也没有因此扩任何吃饭实现。

## 8. 一行结论

整版最后两处增量落地：**重启**从"读盘 probe / 同进程重建"升级为两个独立进程各自装配真实 runtime 并续上同一 Activity
（`13.359s`，exit 0）；**短期评价**从"没有消费者"升级为有界、可审、可空的决策上下文（3 项，`21.319s`，exit 0），
纠正/过期/丢失不消费、空评价 0 调用、不成 Candidate。全部 UNCOMMITTED，未删任何文件；真模型、24h/完整 P2、真并发、
S07、生产 World 包仍未验。
