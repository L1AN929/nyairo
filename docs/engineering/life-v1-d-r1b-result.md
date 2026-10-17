# D-R1b 增补回执：两处未闭点（adopted 精度、真实 history 证据）+ restart 精度更正

- 候选树 `D:\nyairo-v03-backend`｜branch `work/v03-w01-navigation`｜HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（均未移动）
- **全部 UNCOMMITTED**：未 `stage`/`commit`/`reset`/`clean`/`checkout`，未删除任何文件（pyc/socket/log/目录），未整仓复制，
  未跑真实产品模型/外网/外发/24h/部署/profile/provider/凭据；隔离 venv 与 `/tmp/nyaw05r.jdK3q` 唯一根照旧
- 旧 `docs/engineering/life-v1-d-r1-result.md` **保持原文**（历史）；本文件只记本轮两处窄修、一条更正与新的实际证据

---

## 1. 点一（原七点第 3 项）：`adopted` 只记**真正被消费**的那一条

**原缺陷**（R1 回执 §5 已如实登记为"已识别未修"，父级判定不是非阻塞从而必须修）：
`autonomous_life_cycle.py` 旧 `1564-1567` 在 RESUME `APPLIED` 后把
`last_continuity["observed_eligibility_refs"]`（本次 C 读到的**全部** open eligibility）写进
`adopted_eligibility_refs`。演练现场就是反例：`activity_r` 的 eligibility 在 LR-4 仍 `OPEN`，
却会被记成"已消费"。

**修法（最小，不改 schema/装配/准入，不删任何已有文件）**：

| 位置（当前行号） | 内容 |
|---|---|
| `autonomous_life_cycle.py:832` | `ELIGIBILITY_STATUS_CONSUMED = "CONSUMED"`（只镜像 LR-4 词表 `activity_waiting_agenda.py:229`，不写） |
| `autonomous_life_cycle.py:834-877` | 新增 `_resumed_consumed_eligibility(record, adp_rec, int_rec) -> (ref, reason)`：从**已提交 Decision 的选中候选** `selected_candidate_ref` → `runtime.candidate_read.get_candidate()`（`candidate_sources_ag0.py:2826`）→ 候选自带的 `resume_eligibility_ref`（`candidate_sources_ag0.py:1414`）→ LR-4 `get_eligibility()`（`activity_waiting_agenda.py:3238/3244`）→ 必须 `status == CONSUMED`（`:3067` 由 `explicit_resume_from_eligibility` 写）→ 其 `activity_id` 必须等于 integration record 的 `activity_ref`（`life_integration_ag2.py:1800`）。任一环不通即返回 `(None, reason)`，**不写** |
| `autonomous_life_cycle.py:879-897` | 新增 `_owner_eligibility()`：只读 LR-4（waiting_coordinator 优先，退回 eligibility_store） |
| `autonomous_life_cycle.py:1699-1712` | 只在 RESUME + `APPLIED` 时调用；成功 → `adopted_refs=[consumed_ref]` 且轮次记录 `continuity_consumed_eligibility_ref`；失败 → 不写任何 adopted，轮次记录 `continuity_adoption_unresolved=<reason_code>`（不猜、静默不算） |

`seen`（考虑过）与 owner 的 `OPEN`/`CONSUMED` 仍是三件不同事实：`seen` 由已提交 Decision 推进（`:1697`），
`adopted` 只记成功消费的那条，`status`/`read`/`DEFER`/`REJECTED` 一律不推进 `adopted`
（DEFER 分支在 `:1714`，早于任何写）。

**evidence（真实 owner 数据，非单元拼盘）**：`DCoPresenceClosureTest.test_only_the_consumed_eligibility_is_recorded_as_adopted`
（`tests/test_life_v1_integration.py`）在同一条真实链上造出**两条同时 OPEN** 的资格：
private work 被共同时间打断（elig1）→ **真实**跑一次阅读轮产生第二个 Activity → 第二次共同时间把它也打断（elig2）。
断言：`two_open == {elig1: work, elig2: reading}`；DEFER 轮后 `adopted == []`；
`_resume_cognition_for(work)` 真实 RESUME → `adoption_status=APPLIED`、
`continuity_consumed_eligibility_ref == elig1`、`adopted_eligibility_refs == [elig1]`（**恰好一条，不是子集**）、
`elig1` 在 LR-4 `CONSUMED`、`elig2` 仍 `OPEN` 且仍被合法 AG-0 源 `resume_eligible_source.discover()` 提供；
再连做 3 次 `status()` + `read_continuity()` 后，水位与两条资格状态**一字未变**。

## 2. 点二（原七点第 2 项）：去掉合成的 history 证据，改用**真实发生的行**；并更正此前的夸称

**删掉的合成物**（日志保留，文件不动旧记录）：`continuous_drill.py` 旧 `332-359`（直连 Supply 造"第二件作品" +
手写 `ag2.LifeExperienceHandoffEvent(decision_refs=["dec:drill:second"], adoption_refs=[…], action_refs=["ar0act:…"],
settlement_refs=["settle:…"], progress_refs=["prog:…"])`）与 `tests/test_life_v1_integration.py` 旧 `649-658`
同类构造。这些 ref 从未由 owner 产生，**不构成**"真实 Action→M0→后续实际步骤"的证据。

**改为真实链**（一条实例内已经发生的事）：AR-0 动作 → owner receipt（`runtime.action_read.list_receipts()`）→
AG-2 handoff（`life_integration_ag2.py:4390-4410`，`handoff_id = lexp:<sha(activity:kind:v<activity_revision>)>`，`:4345-4350`）
→ M0 准入/回读 → C 读 → 下一步。

**读真数据时暴露的两条 owner 事实（必须按它改断言，不能按想象）**：

1. 真实 M0 行**没有** `version_ref`；行里真实的 refs 是 `activity` / `action`(actn:…) / `artifact` / `owner_result`(`rset:…`)。
   C 的 `next_step.continue_from_ref` 因此是**owner 结果 ref**，不是版本。
2. AG-2 对同一 `(Activity, kind, activity_revision)` **只递交一次**（`life_integration_ag2.py:4345-4359`），
   同一 revision 的第二次提交是幂等重放、**不新增 M0 行**。所以"C 读到的最新行"可能比 owner 当前 head 落后一版。

**据此加的窄语义（`autonomous_life_cycle.py`）**：

| 位置 | 内容 |
|---|---|
| `:953-983` | `_history_continuation()`：拿到 C 命中的那一行的 `continuation_ref`（owner 结果）与 `action_ref`，再从**该行自己的 Action 回执**取版本 |
| `:985-1001` | `_owner_action_version()`：`runtime.action_read.list_receipts(action_ref)` → `details["version_id"]`；读不到就返回 None（不猜） |
| `:1058-1075` | `_read_resume_point()` 新增 `history_continuation_ref` / `history_action_ref` / `history_version_ref` / `expected_head_source="OWNER_CURRENT_HEAD"` / `history_state ∈ {OK, STALE_HEAD, VERSION_UNVERIFIED}` |

语义：**历史只是提示**——版本来自 owner 对那一行 Action 的回执；步进 pin 永远取 owner **当前** head
（`_ar0_step_spec` 的 `expected_head`，`:1649` 区域）；历史行落后 → 记 `STALE_HEAD` 并照常按 owner head 前进
（明确冲突/降级，不硬写陈旧版本）；行里的对象若非法/非作品，仍走既有 `CONTINUITY_ARTIFACT_EXCLUDED/UNVERIFIED`
回退，**Memory 字符串不能 retarget 当前 Activity 的合法对象**。

**证据**：
- `DContinuityIntegrationTest.test_a_real_owner_result_names_the_next_continue_point_and_the_step_lands`
  （旧合成用例已删除并更名）：work 轮真实 ACTED → M0 行（`VERIFIED`）→ 该行 `action_ref` 的 owner 回执版本 == 新 head →
  C 的 `next_step` 指向 `artifact == 原作品`、`continue_from_ref == 该行 owner_result` →
  `selected_by=CONTINUITY_HISTORY`、`history_state=OK`、`history_version_ref == head` →
  `_ar0_step_spec` 的 `artifact_id`/`expected_head` **就是**该对象/该版本 → 下一轮**真的执行**（ACTED、真回执、新版本）；
  随后把同一份**真实旧视图**再喂一次 → `STALE_HEAD` 且 pin 仍是当前 head；弱 cue（无行/无 artifact）→ `BOUND_WORK`、
  对象与 pin 不变。
- 同实例连续演练（`/tmp/nyaw05r.jdK3q/d-r1-recovery-r1b-final.log`）：`history row=lexp:42b56680a28fdd4f
  activity=actv_412b9533dc65b6c8 owner_result=rset:0a9eff1ccf25ea94 head=artifact-version:b6aa900f… sent=…b6aa900f…->…ae9e8c65…`
  —— 即：历史行来自真实 Activity A、对象未被 retarget、pin 取 owner head，随后**真实提交**了新版本。

**对旧 R1 回执的更正（旧文件保持原文，此处点名）**：旧回执 §3 表格第 7 段与 §7 结论写的
"历史改变下一件作品（另一对象）/ owner 已证"应读作：**同一作品/同一 Activity 的下一步**由 admitted history 提供
continuation 输入并由 owner 回执核版本；"把继续对象换成第二件作品"这件事**从未被真实证据支持**（旧证据是手写
handoff + 直连 Supply 写），本增补撤回该说法。

## 3. restart 精度更正：现在是**另一 PID 的只读读盘 probe**，不是全 runtime 重启

- drill 的 `RESTART_SCRIPT`（`continuous_drill.py:54-73`）与
  `DRestartAndQuietTest.test_a_real_second_process_sees_the_same_budget_pause_and_bookmark`
  （`tests/test_life_v1_integration.py:1103-1166`）都是：`subprocess.run([sys.executable, "-c", …])` 读
  `instance_environment / request_budget / status / reading_view` 后打印 `RESTART_PROBE`。
- 原进程仍**持有** canonical Activity authority lease；第二个 runtime 会被直接拒绝
  （`tests/test_one_day_life.py:853-868`，`ActivityLockError` + 入口 `EXIT_LEASE_HELD`）。
- 所以能如实交付的窄范围是：**新 PID、同 data root、同 run identity、同 ceilings、pause 仍生效、书签仍在、未完事项未补造**。
  **没有**"关掉旧 runtime → 重建 runtime/装配 → 继续跑"的全重启证据，本增补不再这样称呼它。

## 4. 本次实际命令 / exit / 耗时（只跑这些）

| 命令 | exit | 结果 |
|---|---|---|
| `run_tests.py r1b-eligibility-final …DCoPresenceClosureTest.test_only_the_consumed_eligibility_is_recorded_as_adopted` | **0** | `Ran 1 test in 13.866s` / `OK`（同一测试此前一次 `r1b-eligibility` 也 OK，16.759s） |
| `run_tests.py r1b-history-final …DContinuityIntegrationTest.test_a_real_owner_result_names_the_next_continue_point_and_the_step_lands` | **0** | `Ran 1 test in 13.899s` / `OK`（此前 `r1b-history` 也 OK，8.848s） |
| `run_drill.sh r1b-final`（同实例连续演练） | **0** | `Ran 1 test in 24.407s` / `OK`，`DRILL OK` |

未重跑（按父级要求）：36 条 C 套件、9 条 inherited、D 文件 55 项、17/86、全仓/World/Supply/Memory/CLI 大套、
`w05rr1c_neg.sh`。`tests/test_one_day_life*` 断言一行未动。本轮改到的共享路径（RESUME 消费记录、`_read_resume_point`）
已被上面两条专项 + 连续演练覆盖（三者都真实经过 RESUME→adoption 与 history 选择）。

现场（保留未清理）：`/tmp/nyaw05r.jdK3q/d-r1-recovery-r1b-eligibility*.log`、`…-r1b-history*.log`、
`…-r1b-final.log`；此前 `drill*.log`（含 drill7-drill12 的中间失败/中间绿）与旧 `d-r1-recovery-drill.log` 全部保留。

## 5. 状态更新（相对 R1 回执）

- **已修**：`adopted_eligibility_refs` 只记真正 CONSUMED 的那条；不可归因时写空 + 明确 reason（不再把"看到的"记成"消费的"）。
- **已修（语义补强）**：history 提供的 continuation 输入 = owner 结果 ref + 该行 Action 回执里的版本；陈旧行 `STALE_HEAD`，
  pin 永远取 owner 当前 head；弱 cue / 非法对象不 retarget。
- **撤回**：R1 回执中"历史把继续对象换成另一件作品（owner 已证）"的措辞，以及"真子进程重启"的说法（应为只读 probe）。
- **仍未验（不夸张）**：真实产品模型行为（演练是 loopback 决策/内容替身）、完整 P2 / 24h / 真实时钟、
  生产 World 包是否声明房间家具、真并发（仍 `DEFERRED_BEYOND_CURRENT_CONTRACT`）、Body 正向事实（owner 缺口，S07 未接）。

## 6. 本轮自有 diff（其余 UNCOMMITTED 是既有）

| 文件 | 状态 | 改动 |
|---|---|---|
| `components/life/scripts/autonomous_life_cycle.py` | 改（UNCOMMITTED） | `:832` 常量；`:834-877` `_resumed_consumed_eligibility`；`:879-897` `_owner_eligibility`；`:953-983` `_history_continuation`；`:985-1001` `_owner_action_version`；`:1058-1075` `history_*` 字段；`:1699-1712` 只写真正被消费的那条 |
| `tests/test_life_v1_integration.py` | 改（未跟踪文件） | 新增 `INVITATION_2`/`END_EVENT_2`、`_resume_cognition_for`/`_reading_cognition`、资格专项；删除合成 history 用例并换为真实 history 用例（弱 cue / 陈旧对照保留） |
| `continuous_drill.py`（scratch） | 改 | 第 7 段去掉第二作品与手写 handoff，改真实行 + owner 回执版本 + 执行落地 + 两种负控；第 8 段诚实标为只读 probe |
| `docs/engineering/life-v1-d-r1b-result.md` | 新增 | 本增补 |
| `docs/engineering/life-v1-capability-status.md` / `life-v1-runbook.md` | 改 | 更正 adopted 与 restart 两处表述 |

（说明：`D:\nyairo-v03-backend\AGENTS.md` 在本次会话期间出现、非本任务创建，未触碰、未删除。）
