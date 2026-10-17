# D-R1 回执：唯一核心施工（同实例连续演练收口）

- 候选树：`D:\nyairo-v03-backend`（WSL `/mnt/d/nyairo-v03-backend`）
- branch：`work/v03-w01-navigation`（未移动）｜HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`（未移动）
- **全部改动 UNCOMMITTED**：本增量**没有** `reset`/`checkout`/`clean`/`recreate`/`stage`/`commit`/`push`，
  没有删除任何文件（含 pyc/临时目录），没有整仓复制，没有跑 `w05rr1c_neg.sh`
- 平台：WSL Ubuntu 隔离 venv `/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/w04venv/bin/python`，
  一律 `PYTHONDONTWRITEBYTECODE=1`；夹具/日志根 `/tmp/nyaw05r.jdK3q` 唯一创建并**保留**
- 未做：真实产品模型、外网/外发、24h/长跑、部署自启、profile/provider/凭据改动、破坏性迁移、递归委派
- 前置：国际 DeepSeek 两次失败（`072e430b…`、`2d69ca29…`）都是通道 `503 no_healthy_account`，
  与代码无关；本次按已批准的国内备选接续既有 UNCOMMITTED 成果，**没有**从审、重写或重建

本文件是**新**回执。旧首轮 `docs/engineering/life-v1-d-result.md`（未接收历史）保持原样不动。

---

## 1. 本次增量到底改了什么（与前次已有修复分开记）

**本次只改了 scratch 演练，没有改任何产品源码。** `git status --porcelain` 与接续时核对完全一致
（仍是既有 W01…R1c + A/B/C/D + 返修那一批 UNCOMMITTED），本增量的自有文件只有：

| 文件 | 状态 | 改动（行级） |
|---|---|---|
| `…/scratch/<id>/life-v1-d-r1-recovery/continuous_drill.py` | 改（scratch，不在仓库） | `:157-165` 装配后把 **C 读口**按 resident 同样的方式接上（`:162` `cycle.continuity_source = lambda: self._continuity_reader(...)`）；`:359-392` 第 7 步改成向 C 提**产品自己的水位**问题（`cycle.read_continuity()`），并新增三条断言（owner 仍 OPEN 的资格集合、该集合 ⊆ 水位 `seen`、`next_step` 必须是 `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT`）；`:136-141` 修掉 no-candidate 调试打印里未定义的 `activity_ref`（NameError 隐患） |
| `…/scratch/<id>/life-v1-d-r1-recovery/run_drill.sh` | 新增（scratch） | 只跑这一条演练并打印子进程**真实** `PYTHON_EXIT`（避免管道/`$?` 被吞） |
| `docs/engineering/life-v1-d-r1-result.md` | 新增（本文件） | 本回执 |
| `docs/engineering/life-v1-capability-status.md` | 改 | 只改被本次演练真正证明/推翻的行 + 文末增量说明 |
| `docs/engineering/life-v1-runbook.md` | 改 | 新增 §8：C 的接线条件与水位语义、连续演练的复跑命令 |

### 1.1 上次那一处 FAIL 的真实原因（不是产品放宽，也不是断言问题）

历史失败（保留未覆盖）：`/tmp/nyaw05r.jdK3q/d-r1-recovery-drill.log` →
`continuous_drill.py:353 self.assertEqual(chosen, second_id)`，`None != artifact:cbdad3b4…`。

排查结论（读码 + 复跑确认）：

1. 失败点前，同一实例已经真实走到：quiet `NO_MATERIAL`/calls 0、work `ACTED` + M0 1 行、
   room `desk_home`/`bed_home` + memo 链接、Body `UNKNOWN`/`NO_BURDEN_EVIDENCE`、
   joint1 `COMPLETED` + `JOINT_ACTIVITY_COMPLETED` + 新 eligibility、自主 reading `page=1`、
   private `RESUME` 保存点保留并 `ACTED`。这些都没问题。
2. 第 7 步时 owner 侧确实还有一件 **WAITING** 的活动（被打断的阅读活动）带着一条**未被考虑过**的
   resume eligibility（`end()` 在 `life_co_presence.py:753` 交出的 `new_eligibility_refs[0]`）。
3. C 的**成文规则**是：WAIT 只要有"调用方还没见过"的新 eligibility，就压过历史
   （`life_continuity_inputs.py:625-647`，`NEXT_STEP_RESUME_WAIT`）。所以 `next_step` 是
   `RESUME_WAITING_NEW_ELIGIBILITY`，`continuity_continue_object()` 依约返回 `None`
   （`autonomous_life_cycle.py:830-872`）——**产品行为是对的**。
4. 演练自己的注释（原 `:298-300`）写的是"再问 C 时**两条 eligibility 都已知**"，但代码实际用
   `reader.read(known_eligibility_refs=(), since=None)`：把"从未考虑过"伪造成"空水位"，
   于是 WAIT 合法地压过历史。**这是演练的失配，不是产品缺陷。**
5. 同时发现演练装配时没给 cycle 传 `continuity_source`，而该实例**确实配了 Memory**
   （`one_day_life.prepare()` 写 `NYA02_MEMORY_DB`，`one_day_life.py:439`），resident 在同样条件下
   是会接 C 的（`autonomous_life_resident.py:427-432`、`:309-350`）。也就是说演练跑的是产品
   **不会产生**的一种接线，然后用手喂的视图替代了 C。

修法（只动演练，断言一条没放松，只加不减）：把 C 按 resident 的方式接上，第 7 步直接用
`cycle.read_continuity()` 让 C 在**产品自己的水位**下作答，并把"水位里真的已经有那条 eligibility"
变成断言（`continuous_drill.py:366-374`），而不是测试侧声称它已知。

---

## 2. 七点验收：实际接点与 owner 证据（当前代码为准）

| # | 要求 | 实际接点（当前代码） | owner 证据 / 演练断言 |
|---|---|---|---|
| 1 | 共同 Activity 真实 owner terminal/release → 新 eligibility 经 AG0/AG1 Decision + AG2 Adoption 恢复原 private checkpoint 并续步；`begin` 只有 `APPLIED` 才算成功；重复 `end` 幂等；atomic/pause 拒绝 | `life_co_presence.begin()`（`:409`）→ LR-3 `assess_events(apply_immediate_pause=True)`；`_park_private_activity()`（`:546`）→ LR-4 `enter_waiting_with_condition(release_foreground=True)`；`_adopt_joint_activity()`（`:613`）→ AG-0/AG-1/AG-2；`end()`（`:679`）→ `_complete_joint_activity()`（`:781`，LR-5 `ingest_progress_evidence(apply_natural_completion=True)`，`:822`）→ 新 eligibility（`:753`）；回 private 走 AG-2 `explicit_resume_from_eligibility`（`life_integration_ag2.py:3382-3452`，调用在 `:3434`） | 演练：`joint1=COMPLETED`、`completion=JOINT_ACTIVITY_COMPLETED`、`eligibility=relig:…` OPEN 且 `llm_calls=0`；`begin` 返回 `adoption_status==APPLIED` 才继续；启动共同活动后 private 活动状态 `WAITING` 且 `checkpoint_ref` 非空；resume 轮 `decision=RESUME`、`adoption_status=APPLIED`、`back["checkpoint_ref"]==checkpoint_a`、随后 `continued=ACTED`。未改任何 owner 合同/白名单/单前台语义 |
| 2 | 真 Action receipt → handoff → M0 admission/control → C → 后续实际对象/步骤变化；弱 cue 不变；三主体绑定不强等；每次读 fresh now；选中 evidence 与真实 artifact:ID 匹配；owner 回读核型/权限/合法目标 | `cycle._ar0_step_spec()`（`:1638`）→ `_classify_ar0_step()`（`:1666`）→ AR-0 适配器 + `_commit_next_version()`（`:2486`）→ AG-2 handoff → M0 consumer；C：`read_continuity()`（`:745-778`，每次 factory 新 binding）→ `continuity_continue_object()`（`:830-872`）→ `_read_resume_point()`（`:881-938`，owner `resume_point()` 回读 `lifecycle`/`head_version_id` 并置 `owner_verified`） | 演练：work 轮 `ACTED` + `owner_receipt` + 真新版本 + M0 行 `delivery_status=VERIFIED`；历史改变对象：`selection["selected_by"]=CONTINUITY_HISTORY`、`owner_verified=True`、`point.artifact_id==第二件`、`head(point.artifact_id)["version_id"]==point.head_version_id`、`_ar0_step_spec()["payload_spec"]["artifact_id"]==第二件`。弱线索/非作品文档由既有 `tests/test_life_v1_integration.py:694-721` 守（未改）。三主体仍分离（`MEMORY_OWNER` vs `test:w05` vs `chiyo.global`） |
| 3 | status/read 不吃资格；只有成功 adopt 才消费；`due_at` 不是消费 since；DEFER/pause/crash 可恢复；quiet 不轮询模型 | `_record_continuity_progress()`（`:780-802`）只在**已提交 Decision** 后由 `:1559` 调用；`adopted` 只在 RESUME `APPLIED` 时写（`:1561-1567`）；`since` 用本次考虑时刻（`:819-820`），不用 C 的 `next_recheck_at` | 演练：quiet 轮 `NO_MATERIAL` + `llm_calls=0` + writer 调用 0；`read_continuity()` 本身不写水位（`tests/test_life_v1_integration.py:749-751`）；owner 侧仍 OPEN 的资格集合**恰好只剩**被打断的阅读活动那一条（`== {elig2: activity_r}`），被 RESUME 掉的那条已 CONSUMED —— OPEN 与 SEEN 是两个集合，互不推断 |
| 4 | 真实阅读 PERSONAL_OPPORTUNITY → AG0 → AG1 → AG2/AR0，owner page + bookmark；作品/读不固定优先 | `artifact_continuation_source.materialize()`（`:230-272`）；`cycle.reading_available()`（`:950`）、`_reading_step_spec()`（`:1006`）、`_classify_reading_step()`（`:1112`）、`_decision_chose_reading()`（`:1003`）；路由 `_RoutedAdoptionSpec`（`:1368-1374`）；落脚 `one_day_life.read_page()`（`:976`） | 演练：`reading page=1`、`understanding=UNKNOWN`（不伪造懂）、private 文档真出新版本（`source_acquisition` + `reading_position`）、M0 里有阅读材料行、阅读活动 `activity_r != activity_a`。作品与阅读在同一次候选窗口同时合法，由 Decision 选，没有固定优先级 |
| 5 | 真实 declared room 对象 + Body read；UNKNOWN 诚实、不要求 AVAILABLE/S07；room 不伪写 owner state | `one_day_life.observe_room()`（`:946`）→ `world_living_skeleton.observe_world`；`link_item_to_room_object()`（`:959`）→ `pdi.note_item_at_observed_object`；`life_situation_view` + `WorldBodyReadPort` | 演练：房间 `available` 且 `objects=['desk_home','bed_home']`（夹具世界**声明**了区域与家具）、`memo:…` → `desk_home` `applied=True`；Body `state=UNKNOWN`、`reason_code=NO_BURDEN_EVIDENCE`、`source=world_body_read_port.get_body_signals`，断言"不是 AVAILABLE"且带真实 reason（Body owner 正向事实仍缺，见 §6） |
| 6 | 真 subprocess 不同 PID；新 budget 对象同盘 counter/ceilings/run/pause/bookmark/unfinished 保持；不同进程才叫 restart；quiet 0 真实 HTTP/模型 | 重启脚本 `continuous_drill.py:54-73` 以 `subprocess.run([sys.executable, "-c", …])` 起**另一个进程**，读 `odl.instance_environment/life_state_root/status/reading_view` + `assembly.request_budget()` | 演练：`probe["pid"]=6526 != os.getpid()`、`run_id` 相同、`utc_day_calls=2` 且 ceilings 与重启前一致、`pause.paused=True` 且 `state_class=PAUSED`、`reading.positions` 非空、`foreground_activity_ref` 仍是未完成的 `activity_a`（没有补造停机期间的事） |
| 7 | 一条同实例连续场景强断言必走成功，禁止 `or True` | `continuous_drill.py:147-430`（一条、同实例、同数据根、8 段） | 见 §3：`Ran 1 test in 12.419s / OK`，`PYTHON_EXIT=0` |

---

## 3. 连续演练（一条同实例生活）完整结果

命令（真实 exit 由脚本打印，不经管道）：

```
bash …/life-v1-d-r1-recovery/run_drill.sh drill6
LABEL=drill6
LOG=/tmp/nyaw05r.jdK3q/d-r1-recovery-drill6.log
PYTHON_EXIT=0        # Ran 1 test in 12.419s / OK
```

同一条演练在本次修复后一共跑绿四次（`drill3`/`drill4`/`drill5`/`drill6`，日志全部保留）：`drill5`、`drill6` 用
`run_drill.sh` 记录了真实 `PYTHON_EXIT=0`，`drill3`/`drill4` 是手工调用（只有 `OK` 输出，exit 未被可靠捕获）。
四次每次的 artifact id 都不同，说明不是靠某个固定夹具 id 撞过的；`drill6` 是加了"owner 仍 OPEN 的资格集合
恰好只剩被打断那条"这条更强断言后的最后一次。逐段实际输出（drill6）：

| 段 | 现场输出 | 断言要点 |
|---|---|---|
| 0 quiet | `outcome=NO_MATERIAL calls=0` | 无观察、无绑定 → 0 模型调用、0 动作 |
| 1 真作品轮 | `work_round=ACTED activity=actv_cd91754993ded008 m0_rows=1` | owner receipt + 真新版本 + M0 `VERIFIED` |
| 2 房间与私人条目 | `room objects=['desk_home','bed_home'] linked=memo:3a6c0eee17cd5f94` | 声明物件真实读到，条目链到真实 object_id |
| 3 Body | `body state=UNKNOWN reason=NO_BURDEN_EVIDENCE` | 诚实 UNKNOWN，明确不是 AVAILABLE |
| 4 显式邀请→完成 | `joint1=COMPLETED completion=JOINT_ACTIVITY_COMPLETED eligibility=relig:216621ac97cb1a03` | 合法中断→合法完成→前台释放→新 eligibility（`llm_calls=0`） |
| 5 自主阅读 | `reading page=1 activity=actv_a16250962bb72e9b doc_head=artifact-version:b6019349…` | 真取得一页 + 真实书签版本 |
| 6 再打断 + 恢复 private | `open_before_cp2=[(actv_cd91…,'WAITING',4),(actv_a162…,'ACTIVE',10)] foreground=actv_a1625…` → `resume=RESUME checkpoint_kept=True continued=ACTED` | Decision+Adoption 消费 eligibility，保存点原样保留并真续步 |
| 7 历史改变下一件作品 | `history chosen=artifact:96f2ab913a8e4bb6ba18b935a2d6dd0b step_target=artifact:96f2ab913a8e4bb6ba18b935a2d6dd0b` | C 用自己的水位 → `CONTINUE_FROM_OWNER_CONFIRMED_OBJECT` → `CONTINUITY_HISTORY` + `owner_verified` + owner head 回读 + 步骤 payload 指向该对象 |
| 8 真子进程重启 | `restart pid=6526 run_id=run:instance calls=2 paused=True status=PAUSED` → `DRILL OK` | 新 PID、同 run identity、同 ceilings、暂停仍生效、书签/未完事项保持 |

现场目录（保留未清理）：`/tmp/nyaw05r.jdK3q/d-r1-recovery-drill6.log`（另有 `drill3/4/5`、历史 `d-r1-recovery-drill.log`、
`d-r1-recovery-r1-cn-c-inputs.log`、`d-r1-recovery-r1-cn-d-continuity.log`），夹具根 `…/xp-*`、`d-r1-pkg-*`、`w05*` 全部保留。

---

## 4. 命令、真实 exit、耗时（本次只跑这些）

| 命令 | exit | 结果 |
|---|---|---|
| `run_drill.sh drill6`（`continuous_drill.py Drill.test_continuous_drill -v`） | **0** | `Ran 1 test in 12.419s` / `OK`；`DRILL OK` |
| `run_tests.py r1-cn-c-inputs tests.test_life_continuity_inputs` | **0** | `Ran 36 tests in 1.115s` / `OK`（C 自己的合同：WAIT 只有新 eligibility 才给恢复、since 水位、零授权） |
| `run_tests.py r1-cn-d-continuity tests.test_life_v1_integration.DContinuityIntegrationTest` | **0** | `Ran 9 tests in 79.528s` / `OK`（C→cycle 接缝、弱线索、第二 writer、暂停 0 模型） |

未重跑（按比例验证要求，也避免把"继承的重复"当进度）：D 文件 55 项、`test_one_day_life` 17 项、
`test_one_day_life_w05r` 86 项、A/B/C 各自定向套件、`check_life_v02.py`、全仓/World/Supply/Memory/CLI 大套、
`w05rr1c_neg.sh`（禁止执行）。本增量没有改 repo 源码，故不重跑这些套件；`tests/test_one_day_life*` 断言一行未动。

---

## 5. 已识别、本次**没有**修的一处记录精度问题（如实登记）

`autonomous_life_cycle.py:1564-1567`：RESUME `APPLIED` 之后，`adopted_refs` 写入的是
`last_continuity["observed_eligibility_refs"]`（本次 C 读到的**全部** open eligibility），
而不是真正被 owner 消费的那一条。演练现场就是例子：`activity_r` 的 eligibility 仍 OPEN，
却会被写进 `continuity_watermark.json` 的 `adopted_eligibility_refs` —— 按第 3 条验收的语义
（"只有成功 adopt 才消费"）这是**过度声明**。

为什么不顺手改：

1. 该字段目前**没有任何消费者**（`read_continuity()` 只把 `seen` 与 `since` 交给 C，
   `autonomous_life_cycle.py:767-770`），改错方向会先丢掉现有记录、换不来行为收益；
2. 正确的唯一真源是 **LR-4 owner 自己的 CONSUMED 状态**（或选中候选的 `resume_eligibility_ref`，
   `candidate_sources_ag0.py:1414`）。cycle 拿得到 `out["candidate_set"]`，但 `CandidateSet`
   只带 ref 列表（`candidate_sources_ag0.py:699-735`），拿不到候选记录，所以要么再读一次 LR-4，
   要么给装配多透一层候选映射 —— 这已经超出"最小修"。

建议的窄修（下一增量，需批准）：resume 之后按 Activity + `checkpoint_ref` 从 LR-4 回读**已 CONSUMED** 的
eligibility，只写那一条；读不到就写空，不猜。

---

## 6. 已接 / 未接 / 未验 / 真实阻塞

**已接（本地真实 owner + 真实 World + loopback 决策分支，可复现；本次由一条同实例连续演练证明）**
1. 已接：真实 AR-0 Action → receipt → AG-2 handoff → M0 admission → C 读 → **后续步骤对象真的换掉**
   （`CONTINUITY_HISTORY` + owner 回读 + 步骤 payload 指向第二件作品）。
2. 已接：合法共同活动 completion（LR-5 `ingest_progress_evidence`）→ 真实 owner terminal →
   新 resume eligibility → Decision+Adoption `RESUME` → 原 private checkpoint 保留并续步。
3. 已接：status/read 不吃资格；只有 `APPLIED` 才消费；quiet 轮 0 模型；pause 与真子进程重启接续。
4. 已接：房间声明物件 + 私人条目链接（夹具世界**真的声明了**区域/家具）；Body 诚实 UNKNOWN。
5. 已接：自主阅读一页（owner head + 内容 + 取得范围/书签/未懂 + M0 行）。

**未接 / 未验（如实，不代替）**
1. **真实产品模型行为未验**：整条演练用的是 loopback 确定性决策/内容替身；C 的候选是真实 owner 读，
   但"她会不会这样选"没有真实模型证据。
2. **Body 正向事实仍缺（A 的结论，本增量未改）**：真实身体只有 `LIMITED`/`UNKNOWN`；
   `UNKNOWN`/`NO_BURDEN_EVIDENCE` 是 owner 侧缺口，不是接线问题。S07 仍未接，未扩修。
3. **完整 P2 / 24h / 真实时钟短跑未验**：本增量只有一次 20.8s 的同实例连续演练，不能推出"自主生活一天"。
4. **生产 World 包**：演练用的声明式场景包是夹具（`DScenarioBase.scene_doc()`）；生产 `coastal_town`
   包是否声明了房间家具，未在真实部署包上复验。
5. **`adopted_eligibility_refs` 过度声明**（§5）：已定位、已给定窄修方案，未改。
6. 真并发仍明确延期（`DEFERRED_BEYOND_CURRENT_CONTRACT`）；外发/部署/自启/多实例/凭据均未涉。

**真实阻塞（不是本增量能解的）**
- 国际 DeepSeek 通道两次 `503 no_healthy_account`：属通道，不是代码；本次用已批准的国内备选接续，
  没有改 provider/profile/凭据。

---

## 7. 一行结论

上一轮的 FAIL 不是产品放宽点，而是演练用"空水位"替 C 提问：WAIT 的合法优先规则（`life_continuity_inputs.py:625-647`）
在这种问法下必然压过历史。把 C 按 resident 的方式真实接上、让 C 用自己的水位作答后，**一条同实例连续生活**
（quiet → 真作品+M0 → 房间/条目 → Body → 共同活动完成+新 eligibility → 自主阅读 → RESUME 保存点续步 →
历史改变下一件作品 → 真子进程重启）在 `12.419s` 内全部强断言通过（`PYTHON_EXIT=0`），
全程 UNCOMMITTED，未删任何文件；真模型、完整 P2/24h 与 `adopted` 记录精度仍是**未验/待修**。
