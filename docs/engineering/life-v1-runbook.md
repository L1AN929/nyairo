# life-v1 runbook：一个入口跑同一实例的生活（D 包）

入口只有一个：`components/life/scripts/one_day_life.py`。它自己不写 World/Supply 的任何状态：
`prepare` 走 owner 自己的命令，`run` 驱动既有 `autonomous_life_resident`，其余动词要么只读，
要么只写"Life 自己的东西"（life 侧暂停位、私人事项文档里的条目、协议会话记录）。

## 0. 前置

- POSIX（`fcntl` + `AF_UNIX`）；本包在 WSL Ubuntu + 隔离 venv 上验证
- 一个真实的 Life Supply owner（governance + supply 同一个 Unix socket）与一个 World home
- `REAL_WORKSPACE_PROVISION=ON`、`REAL_ARTIFACT_ACTION=ON`（owner 服务进程自己的开关）
- 想跑真实模型才需要 `NYA02_MODEL_*`；不配模型时 `run` 会在装配阶段拒绝（`NO_CONTENT_WRITER`），
  这是设计：**不**用替身冒充生产写作

## 1. 准备一个实例

```bash
PY=.../w04venv/bin/python            # 隔离 python
$PY components/life/scripts/one_day_life.py prepare \
  --instance /tmp/nya-inst/one \
  --supply-socket /run/life-supply.sock --governance-socket /run/life-supply.sock \
  --subject chiyo --title "草稿" \
  --reading-title "一本可读的材料" \
  --reading-content "第一页…
%%PAGE%%
第二页…" \
  [--create-private-items]           # 想 prepare 时就建 typed 私人文档
  [--memory-db /path/m0.sqlite --memory-controls-db /path/controls.sqlite --memory-subject chiyo]
```

产物（都在实例根里）：`instance.json`（三件绑定 + 房间观察 + 环境）、`world-home/`、`life/state/`。
- `CHIYO_LIFE_WORK_ARTIFACT_ID` = 作品（继续对象，**不**按最新选）
- `CHIYO_LIFE_READING_ARTIFACT_ID` = 阅读材料（页用 `%%PAGE%%` 分隔；没写标记就是 1 页）
- `CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID` = typed 私人事项文档（也可稍后按需创建）
- 房间观察是 prepare 时刻真实读到的 World 投影（`world_living_skeleton.observe_world`），
  World 没声明局部环境时就是"没有物件"，不会编造

`prepare` 默认**不**创建私人事项文档，这样单作品实例的行为与旧版完全一致；需要时加
`--create-private-items`，或让第一次 `note`/`read` 按需创建。

## 2. 跑

```bash
$PY components/life/scripts/one_day_life.py preflight --instance /tmp/nya-inst/one
$PY components/life/scripts/one_day_life.py run --instance /tmp/nya-inst/one \
    --poll-seconds 1800 --duration-seconds 3600            # 或 --once 只跑一个窗口
```

`run` 每次都会把两个请求上限（UTC 日 / run 窗口）写进实例；**重启不会**开新窗口，
只有显式 `--new-run-window` 才会。同一 state root 只允许一个 writer：第二个 `run` 返回
`EXIT_LEASE_HELD`。

## 3. 看

```bash
$PY ... status   --instance /tmp/nya-inst/one     # state_class + 作品/阅读/私人事项/处境/续接
$PY ... timeline --instance /tmp/nya-inst/one --limit 50
$PY ... report   --instance /tmp/nya-inst/one --duration-seconds 86400
```

`status.state_class` ∈ `IDLE / WAITING / PAUSED / BLOCKED / RUNNING / STOPPED`，并带 `basis` 与 `source`；
它只描述技术状态，不是人格/关系分数。`timeline` 只列真实记录（resident journal、cycle step journal、
operator actions、pending UNKNOWN），停机期间的缺口不补。

## 4. 私人生活

```bash
# 读一页（真实读 owner 的版本与内容，然后才记录取得范围、书签、未懂）
$PY ... read --instance /tmp/nya-inst/one --page 2 [--unclear "这一页没懂"]

# 备忘（正文私有，默认视图不回）
$PY ... note --instance /tmp/nya-inst/one --memo "今天想先把开头重写"

# 收藏（不取全文、不置已读）
$PY ... note --instance /tmp/nya-inst/one --reference-source-id artifact:xxx \
      --reference-source-revision artifact-version:xxx --reference-label "以后再看" \
      --reference-receipt-ref receipt:xxx

# 房间（真实 World 投影；只读）
$PY ... room --instance /tmp/nya-inst/one
$PY ... room --instance /tmp/nya-inst/one --item-id memo:xxx --room-object <真实观察到的 object_id>
```

阅读材料被推进新版本后，旧 revision 的书签在元数据里显示 `needs_reread=true`（不会被静默改指到新版本）。

## 5. 有限共同时间

```bash
# 只读：看会话状态与协议规则；普通消息永远是"只观察"
$PY ... invite --instance /tmp/nya-inst/one
$PY ... invite --instance /tmp/nya-inst/one --invitation '{"event_kind":"CO_PRESENCE_INVITATION",
  "invitation_id":"inv:1","source_ref":"channel:desk","occurred_at":"2026-10-16T06:00:00+00:00",
  "scope":"一起待一会儿","subject_id":"chiyo","home_ref":"<life state root>"}'
```

`begin`/`end` **需要持有 canonical lease 的 runtime**，所以 CLI 的 `invite` 对邀请返回
`RUNTIME_REQUIRED`（不会开第二个 writer）：由 resident 或集成测试用它自己的 runtime 调
`life_co_presence.FiniteCoPresence.begin()/end()`。协议顺序是固定的：

1. 普通入站消息 → 观察，不 pause、不新开 Activity
2. 显式邀请 → `consider()`（真实读：LR-3 life view / 暂停位 / 预算 / 前台）→ ACCEPT / DEFER / DECLINE
3. ACCEPT → LR-3 `assess_events(..., apply_immediate_pause=True)` → 规范暂停（原子前台直接拒绝，不强切）
4. 私人工作进入 LR-4 的 WAITING 保存点（`waiting_on_ref = wait_dep:co_presence-<id>`，`release_foreground=True`）
5. 邀请作为 typed user request 进 AG-0 → AG-1 Decision → AG-2 Adoption → 有限共同活动（仍是**单前台**）
6. 显式结束事件 → LR-4 `evaluate_event` → 私人条件满足 → **新 resume eligibility**
7. 回保存点必须再走一次 Decision+Adoption（协议不代做，也不写 receipt）

现在的收口（D-R1）：显式结束事件作为 **LR-5 外部结果证据**真的走 owner 完成
（`_complete_joint_activity()` → `ingest_progress_evidence(apply_natural_completion=True)`），
返回 `joint_completion.reason_code = JOINT_ACTIVITY_COMPLETED` 且 `completed_transition=true`；
重复 `end` 由 LR-5 幂等与协议侧会话状态处理。仍明确延期的只有真并发（`DEFERRED_BEYOND_CURRENT_CONTRACT`）。

## 6. 暂停与恢复

```bash
$PY ... pause  --instance /tmp/nya-inst/one --reason operator
$PY ... resume --instance /tmp/nya-inst/one --reason operator
```

只写 Life 侧的 `<life state root>/life_pause.json`，**不碰** World 自己的 `world_control.json`。
暂停期间不产生新决策、不花请求预算；重启后暂停位仍然生效。

## 7. 回滚 / 注意

- 本包全部改动 UNCOMMITTED；不要 `git add`/`commit` 去"固化"它
- 夹具与临时目录进独立 scratch/`TMPDIR`，不要清理别人的根目录
- 真实模型、24h 长跑、外发、部署自启都需要单独授权

## 8. C（有来源过去）怎么接，水位是什么意思

- C 读口只在实例**配了 Memory** 时建立：`resident._continuity_source()`（`autonomous_life_resident.py:309`）
  用 `NYA02_MEMORY_DB`/`NYA02_MEMORY_CONTROLS_DB`/`NYA02_MEMORY_SUBJECT` 建 `LifeContinuityInputs`，
  并在 `start()` 里作为 factory 传给装配（`:427-432`）。subject 是 **Memory owner**（默认 `chiyo`），
  与 canonical Activity subject（`chiyo.global`）和 Supply workspace subject 是三个不同概念，不强行相等。
- 水位落在 `<life state root>/continuity_watermark.json`：`seen_eligibility_refs`
  （**已提交 Decision** 的轮次考虑过的资格）、`adopted_eligibility_refs`（**只**记真正被 LR-4 标为
  `CONSUMED` 且与本次 Adoption 的 Activity 匹配的那一条；不可归因时留空并在轮次里写
  `continuity_adoption_unresolved`）、`eligibility_watermark`（上次考虑的时刻，**不是** C 的
  `next_recheck_at`）。**`status`/`read` 不推进任何一项**；`DEFER`/`WAIT`/`PAUSE`/`NO_ACTION` 都是**已提交的 Decision**，
  因此会推进 `seen` 与 `since`（"考虑过"），但**只有**真正被 owner 消费的 RESUME 才推进 `adopted`；被拒或未 `APPLIED`
  的 adoption 不推进 `adopted`，也不留"消费"记录。
- 规则（`life_continuity_inputs.py:625-647`）：一个 WAIT 只有在"调用方还没见过"的新 eligibility 存在时
  才压过历史；否则 `next_step` 走 `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`，把**继续输入**（owner 结果 ref）换成
  owner 确认过的那条。历史只是提示：步进 pin 永远取 owner **当前** head；那一行的版本从**该行 Action 的 owner 回执**
  取（`details["version_id"]`），读不到就是 `VERSION_UNVERIFIED`，落后就是 `STALE_HEAD`，两种情况都照常按 owner head
  前进——不硬写陈旧版本，也不把当前 Activity 的合法对象 retarget 成 Memory 里的字符串。
- restart 证据分两层，不要混着说：①**只读读盘 probe**（另一 PID 读 run identity / 计数器 / pause / 书签）——
  `DRestartAndQuietTest.test_a_real_second_process_sees_the_same_budget_pause_and_bookmark` 与旧 drill 第 8 段；
  ②**真实跨进程重启**——`tests/test_life_v1_restart.py`：worker A 装配真实 runtime（含 Memory 边界与实例自己的预算对象）
  真的跑一步、写下 pause 与计数器后 `close()` 释放 canonical lease；worker B 新 PID、同 data root、新 runtime/新装配/
  新预算对象，先证明暂停态 **0 调用**，再 resume 并合法续同一 Activity 出真实新版本，run identity/计数器/上限不变、
  书签保持、空档期无写入。两者仍都不是"关掉旧 runtime → 重建 → 继续"的驻留进程重启。
- 短期评价（A 的 `short_appraisal_view`）已接成**有界考虑上下文**：`cycle.appraisal_source`（callable → A 的视图）
  → `cycle.appraisal_context()` → 该轮 `prior_experiences`（每项带 `appraisal_ref`/`source_refs`/`valid_until`/
  `declared_scope`/`uncertainty`，且 `authority=None`、`candidate=False`、`stable_fact=False`）。只有投影仍判 `VALID`
  的项会被消费；纠正/过期/来源丢失 → 空，且**不产生**额外模型调用（空评价不会开一轮）。树里没有 S07 owner，
  resident 传 `None` 并如实报 `appraisal_wired: false`——不生成自称的情绪；显式实验输入只用于测试。
- 自己写演练/复现时：**不要**用 `known_eligibility_refs=()`、`since=None` 去问 C —— 那等于宣称"什么都没见过"，
  会让一个合法 WAIT 压过历史。要么用 `cycle.read_continuity()`（产品自己的水位），要么如实传入运行已考虑过的 ref。

复跑 D-R1 的那条同实例连续演练（scratch，不在仓库）：

```bash
bash …/scratch/<session>/life-v1-d-r1-recovery/run_drill.sh <label>
# 打印 PYTHON_EXIT=…；日志 /tmp/nyaw05r.jdK3q/d-r1-recovery-<label>.log
```

复跑本轮两条定向测试（整版默认只跑这两条，不重跑大套）：

```bash
$PY -m unittest tests.test_life_v1_restart.RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity
$PY -m unittest tests.test_life_v1_appraisal_context.AppraisalContextTest.test_a_valid_sourced_appraisal_reaches_the_decision_context \
    tests.test_life_v1_appraisal_context.AppraisalContextTest.test_a_correction_or_expiry_keeps_the_appraisal_out_of_the_context \
    tests.test_life_v1_appraisal_context.AppraisalContextTest.test_an_empty_appraisal_costs_no_call_and_never_becomes_a_candidate
```

（两条用例都继承同一 `DScenarioBase`，所以**按 id 跑**；直接跑模块会连带收集基类继承下来的 W05 三条。）
