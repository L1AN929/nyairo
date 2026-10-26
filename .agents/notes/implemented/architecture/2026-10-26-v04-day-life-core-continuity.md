# Agent Note: v0.4 一天生活 —— 核心连续性（步骤就绪事实 + 具名无步骤 + 真实退出核对 + 有限再考虑）

Status: implemented

## Problem

0.4 的目标是主体正常活一天。AG-1 已经把"必被拒的决策"关掉了（`2026-10-26-v04-day-life-decision-legality.md`），
但核心循环本身还有四个真实缺口，全都出现在"这一轮真的被考虑了"之后：

1. **有材料 ≠ 有步骤。** 材料读尽、来源被撤、作品 writer 在同一版本上明确 `no_change` 之后，owner 里那条
   Activity 仍是 `ACTIVE`，`CONTINUE` 仍合法，但执行时没有任何一步能跑。AG-1 需要一条**有来源、只描述
   当前活动**的事实来撤掉这一条 CONTINUE，而它当时不存在（生产者不在 A 的写集）。
2. **无步骤被写成"她没做"或"她做了"。** `evaluate_once` 在"前台是阅读但没有页"的分支返回 `NO_ACTION`，
   在"采纳了但这一版没有窄步骤"的兜底分支返回 `ACTED`。前者把 unavailable 说成她的选择，后者更直接：
   什么都没执行却报成动作。
3. **退出报告不核对 owner。** 通用 PAUSE/ABANDON 分支直接把 Decision 翻成 `PAUSED`/`ABANDONED`，不看
   `adoption_status`，也不回读 Activity。被拒也报"她放弃了"，等于对过去撒谎。
4. **同一状态反复花钱。** 作品 `no_change` 只在内存里；下一轮同一个"读尽/无话可说"的状态会再花一次决策
   请求，一天可以把预算烧在同一件已经问过的事上。

另外两件必须一起处理，否则"暂停后还能活"和"身体动作"都接不上：

* PAUSE 之后前台释放，别的对象的机会照常可以开始；但**旧目标被 PAUSED 时不能靠新建第二个 Activity 绕过
  resume 资格**（LR-4 的资格 + owner 的 RESUME 才是那条合法路）。
* W 提供了 `bodyact:` 目标与它自己的解析/构建/观察函数；cycle 必须复用它们，不能自己发明编码，也不能把
  "命令成功"当成"姿势成立"或"她恢复了"。

## Decision

只改 B 的独占写集：`autonomous_life_cycle.py`、`autonomous_life_runner.py`、
`artifact_continuation_source.py`，新增窄支持模块 `life_step_readiness.py`。没有新 owner、没有新 schema、
没有第二前台、没有 LifeManager，也没有提高任何轮数/请求上限。

1. **只读 step readiness（新模块）**：`assess_step_readiness(...) -> StepReadiness`，`available` 是三态
   （`True` 有步骤 / `False` 确实没有且 `reason_code` 具名 / `None` 不知道），并且**必须绑定**
   （`activity_ref` + `target_ref`）才算一条可陈述的事实。判据按 target 命名空间分派：`stay:` 是内部
   checkpoint；`artifact:` 走阅读情境（`EXHAUSTED`/`REVOKED`/…）或该版本上的 `no_change`；`place:`/
   `object:`/`bodyact:` 看这个世界进程到底有没有 World step adapter。唯一对外形状是 AG-1 已消费的
   `current_step_unavailable_true`（ASCII 键、值就是布尔 `True`），只经既有 `observed_facts` 通道，并用
   `merge_priority_facts` 放进 24 键预算的最前面，避免被地点事实挤掉。
   **（父级 gen3 短链抓到并已修；顺序已被下一条 gen4 规则取代：owner 记录优先、候选第二）** 目标归属只从
  Activity 自己的 lineage 取：`source_refs` 里的 `cand:` →
   该 Activity 被采纳时的那个候选的 target；读不到候选且只剩唯一已知 ref 才用它；多个 ref 无法判定 → `None`
   （绝不取"第一个"，那会把地点当成 POSE 的目标）。revision 用 owner 自己的拼写读（`artifact:<hex>` +
   `read_head`，回退 `resume_point`）：裸 id 在 owner 那里是 `NOT_FOUND`，会把每个作品状态降级成"没有
   revision"。START 采纳期就 `no_change` 时没有前台可绑，此时按本轮**所选工作**（候选 work + owner head）
   记录 target+revision，仍不冒用其他 Activity。
  **（父级 gen4 短链抓到并已修）** 目标归属**先**读 Activity owner 自己持久化的单值绑定
  （`target_ref` → `checkpoint_ref` → `origin_ref`，再才是 `subject_refs` 里**唯一**的工件对象），
  候选只作为第二来源、且只在该候选恰好指**一个**可执行 target 时采用；候选 cache 是进程内读，
  重建后为空，不能当作「没有目标」。同一对象本树拼 `artifact:artifact:<hex>`、owner 拼
  `artifact:<hex>`，一律折成 owner 的拼写（`life_step_readiness.canonical_target_ref` /
  `owner_artifact_id`，不掩码、不截断）再判断、落账、查回，所以 START 期记的 `no_change` 在重建后
  仍能被同一个 target 的 readiness 读回；`artifactversion:` 是 revision，永远不是 target。
  世界侧 ref（`place:`/`stay:`/`bodyact:`）不走这条工件路径：一个 World Activity 的 `origin_ref` 是它的
  **所在地**，借来当步骤目标就会把 POSE 说成 street（见 `_owner_record_target_ref` 的命名空间约束）。
2. **具名无步骤**：新 outcome `OUTCOME_NO_STEP_AVAILABLE`。前台阅读没有页、或采纳后这一版没有任何步骤时
   返回它，附 `no_step.reason_code`；runner 映射 `(WAKE_NO_STEP, "blocked")`。它既不是 `NO_ACTION`，
   也不是 `ACTED`。
3. **退出双核对**：PAUSE/WAIT/ABANDON 统一走 `_report_chosen_exit`：先看 Adoption 是否 `APPLIED`，再回读
   Activity owner 自己的状态。`REJECTED*` → `ADOPTION_REJECTED`；`APPLIED` 但仍 `ACTIVE` →
   `STEP_UNSETTLED`；只有 owner 真的落到 `PAUSED/WAITING` 或 `ABANDONED/CANCELLED/EXPIRED` 才报
   `PAUSED`/`ABANDONED`。记录里写 `exit.applied` / `exit.owner_status` / `exit.expected_owner_statuses`。
4. **有限、持久、可失效的再考虑台账**（同模块 `ReconsiderationLedger`，一个文件两种记录）：`entries` 键控
   「target + 该 target 的 revision + 原因」（最多 8 条），`situations` 键控**整个窄处境指纹**
   （`situation_fingerprint`：前台 Activity 及其状态/类型、步骤 target、**工件** revision、阅读状态/revision/
   下一页、作品 head、所在地/姿态/朝向、机会 target 集合；最多 8 条）。都寿命 6 小时、可显式解除，只存
   fingerprint/parts/引用/时间/计数 —— 不存正文、不存理由、不存 Decision，因此既不能重放决策，也不是任务库。
   命中时 cycle 在 `budget.spend()` **之前**返回 `OUTCOME_QUIET`（`llm_calls=0`，runner 记 silence 且不计入
   `wakes_run`）。解除路径：状态真变了（新页面/新 head/新前台/新所在地或姿态/**机会 target 集合变化** →
   不同 fingerprint）、显式 `release_reconsideration(refs=...)`、以及**命中自己 ref** 的真实事件（runner 只对
   `KNOWN_EVENT_KINDS` 且 ref 命中台账 target/activity 的事件解除）。作品 `no_change` 用 `entries` 落盘
   （reason `WORK_NO_CHANGE_CONFIRMED`，绑定 owner head），下一轮读回即知。
   **（父级 gen3 短链抓到并已修）** 旧判定是"无步骤 + 这一轮没有别的机会"，而世界机会几乎每轮都在，于是同一
   状态永远不安静；现在机会"存在"不再是否决理由，机会**集合**是处境的一部分。世界 revision 单独跳动**不**算
   新处境（tick 不是生活事件），工件 head 变化算。
5. **暂停后不绕 resume**：`ArtifactContinuationSource` 增可选 `paused_target_provider`；目标已有 PAUSED/
   WAITING Activity 时不发新候选（`PAUSED_TARGET_REQUIRES_RESUME`），`refresh` 时 withdraw。cycle 用
   Activity owner 的 `list_open_activities()` 提供这个集合。旧停点、resume 资格、LR-2/LR-4 一行未改。
   **（2026-10-26 交叉反例修，同规则的 extra 路径）** 同一来源的到期事项 / 项目选项
   （`_extra_raw_items`，`fitem:`/`popt:`）此前只检查 ref 前缀与 target 缺失，没有套这条规则：
   到期事项指向一个已 PAUSE/WAIT 的同一作品时仍会产出 `FUTURE_ITEM_DUE` 候选并合法 `START`，
   正式链上真的开出第二个 Activity 并写出版本。现在按 owner 目标身份（`artifact:` 前缀折叠）
   在 `_extra_raw_items` 与 extra 分支的 `refresh` 里同样跳过/撤回，原因不变；事项本身仍 OPEN/due，
   `popt:` 与可续作品的正控不受影响。证据见
   [交叉点说明](../../../../docs/engineering/v05-due-parked-target-20261026.md)。
6. **复用 W 的 bodyact**：`_chosen_target_ref` / `_candidate_is_world` / `_world_step_spec` 识别
   `bodyact:`；步骤由 `build_body_action_step(...)` 生成，采纳规格用 `parse_body_action_target(...)` 的
   `fact_key`/`expected_value` 走 LR-5 原 `WORLD_FACT_MATCH` + `WORLD_ACTION_BOUNDED`/`TARGET_STATE`；
   完成判据由 `body_action_observed_fact(...)` 给出 —— POSE 看真实 snapshot 的 `pose`+`orientation`，
   REST 只看 REST 会话状态（`get_available_actions` 的 `current.recovery_state`）。不满足 → `STEP_UNSETTLED`，
   不写进度、不报完成。模块缺失时是具名 `BODY_ACTION_TARGETS_MODULE_UNAVAILABLE`，不自造编码。
   **（父级真实 owner 链 gen2 抓到并已修）** 完成判据必须来自**动作之后**的一次 fresh owner 读取：
   `_classify_body_action` 先走 `_fresh_world_body_observation()` —— 用同一个 observation seam
   (`provider()`) 重新观测一次，拿到动作后的 canonical snapshot 与动作后的 REST availability，
   再交给 W 的 `body_action_observed_fact`。动作前的那次 snapshot 一律不算答案，target ref 也不算
   （target 说的是"要什么"，不是"owner 报了什么"）。重读失败 → 空 snapshot + 无 REST 项 →
   `STEP_UNSETTLED`，并在 `body_action.observation` 里写 `reason_code`/`re_read`/`world_revision`。
7. **R 的证据透传**：cycle 每轮给出 `owner_evidence`（`owner_version_created`/`owner_version_id`/
   `step_path`），`owner_version_created` 只有 owner 真回了版本/checkpoint/收据才是 `True`，可证明没写 owner
   的结局是 `False`，可能写了却不知道是 `None`；runner 原样放进 `wake.extra`，绝不从 `ACTED` 推断版本。

## Alternatives considered

* **让 cycle 自己替她关掉读尽的 Activity（自动 COMPLETED/ABANDON）。** 最强立论：读尽就是读完了，替她收尾
  最省事、最不会卡。否决原因：这正是设计 19 禁的"Action 成功即 Activity 完成 / 脚本代替主体决定"。读尽只
  说明**没有下一步**，收尾是她的选择；本包只把 CONTINUE 撤掉、把退出留住。
* **把 `step_available` 放进容量快照或候选字段。** 最强立论：模型一眼能看到，还省一次事实合并。否决原因：
  那是给决策模型/schema 加字段，而且"没有步骤"不是容量事实（容量是身体/设备/资源/隐私）。走既有
  `observed_facts` 通道零 schema 变更，A 也已经只认那一个键。
* **用定时器/TTL 到期后自动继续或自动结束读尽活动。** 最强立论：卡住的东西需要有人推动。否决原因：定时器
  等于自主生活是明令禁止的；本包只让"同一状态"不再重复花钱，状态一变就自动放行，没有任何自动动作。
* **把再考虑台账做成"任何不行动结果都抑制"（只看"问过没有"）。** 最强立论：安静就是安静，问过一次就别再问。
  否决原因：那会一刀切屏蔽合法退出与真实新机会。最终口径是**整个窄处境的指纹**：同一前台/同一 target 与
  revision/同一阅读与作品状态/同一所在地与姿态/同一机会**集合**才安静；任何一项真变化都是新问题。
  机会"存在"从来不是理由（世界机会几乎恒在），机会**集合变化**才是。
* **PAUSE 时清掉台账或把前台直接置空。** 最强立论：状态变了，抑制自然该失效。否决原因：清台账属于"顺手
  删状态"，而前台归 LR-2 所有；本包只读，PAUSE 成功时 fingerprint 自然变化（activity/状态变了），不需要
  谁去删。
* **在 cycle 里自己解析 `bodyact:`（比如按冒号切三段）。** 最强立论：少一个跨模块依赖，改起来快。否决原因：
  编码归 W，双份解析必然漂移；本包只调用 W 的 `parse`/`build`/`observed_fact`。

## Consequences

* 读尽/撤权/`no_change` 之后：`observed_facts` 里出现 `current_step_unavailable_true`，AG-1 撤掉当前
  CONTINUE，PAUSE/ABANDON/DEFER/NO_ACTION 全部保留；她选 CONTINUE 也不会被记成 `ACTED`/`NO_ACTION`，而是
  具名 `NO_STEP_AVAILABLE`（blocked）。
* 退出报告不再能假报：被拒是 `ADOPTION_REJECTED`，应用了却仍 ACTIVE 是 `STEP_UNSETTLED`。
* 同一状态只花一次请求，**跨进程也算同一次**（台账在 cycle 自己的 state root 里）：新页面/新 head/新前台/
  新所在地或姿态/机会**集合**变化/自己的事件都会让请求重新发生，所以"安静"不是"停机"，也没有靠隐藏世界或
  禁 owner 来实现。世界 revision 单独跳动不重开（tick 不是生活事件）。
* 每轮多两次 owner 读（Activity 记录 + 该 target 的 revision），零额外模型调用；`QUIET`/`NOT_DUE` 连续出现
  时日志只写第一条，长时间轮询不再刷屏。
* 已知代价与残留：
  1. 台账有两类记录（`entries` 键控 target+revision、`situations` 全处境指纹），各 ≤8 条；升级前的旧文件
     只有 `entries`，读时按空 `situations` 处理，不会误抑制。
  2. `life_reconsideration.json` 的 6h 寿命是常量，没有 env 覆盖。
  3. REST 判据依赖 World 暴露 `REST.current.recovery_state`；不暴露就是 `STEP_UNSETTLED`，不是完成。
  4. `QUIET` 是新增 wake 状态，下游按旧枚举穷举时需要按 `status_class="silence"` 处理。
  5. 世界 revision 被有意排除在处境指纹之外。若将来某个 World 版本把"真实变化"只体现在 revision 而不体现在
     place/pose/机会上，需要另加显式感知字段，而不是把 revision 塞回指纹。
  6. `_work_no_change` 若既无前台绑定、又拿不到 owner head（port 读失败），仍不记录任何东西
     （`work_no_change.recorded=None` 明示）：宁可下一轮再问一次，也不凭猜测绑定版本。
  7. `tests/test_life_stay_early_exit.py` 的"非法退出"负控此前是红的，由 testwriter 只改测试负控的选候选
     逻辑修好（产品代码未动，归因与实测见 result 文档 §5.1）；不在本写集。

## Verification

* `tests/test_day_life_continuity.py::DayLifeContinuityTest` 19 个 exact IDs：原 15 全 `PASS`（`EXIT=0`，
  `scratch/b-gen3-new-continuity.log`）；父级 gen3 回归修复后**新增 4 条 + 确实受影响 4 条**共 8 个 exact
  IDs 全 `PASS`，`EXIT=0`（`scratch/b-gen8-parentfix.log`，同代 `b-gen6`/`b-gen7` 的失败与中间态日志保留）。
  新增：`test_a_no_change_on_a_new_start_is_recorded_against_the_real_owner_head`、
  `test_a_body_action_readiness_target_comes_from_its_own_candidate_not_the_first_subject_ref`、
  `test_the_same_situation_in_a_new_process_is_quiet_with_world_opportunities_visible`、
  `test_a_real_new_opportunity_reopens_the_question`。
* **gen4（目标 identity / 重建）**：新增 2 条 + 确实直接关联 7 条共 9 个 exact IDs 全 `PASS`，`EXIT=0`
  （`scratch/b-gen9-target-identity-fix.log`）。新增：
  `test_a_work_target_survives_a_rebuild_where_the_candidate_cache_is_gone`（真实形状：双前缀候选 +
  第二个进程候选 cache 为空 → 同一状态 `QUIET`、`llm_calls=0`、世界机会仍可见）、
  `test_the_domain_wrapper_and_the_owner_id_are_one_target_across_a_rebuild`（一种拼写落账、另一种拼写查回）。
  同代红证保留：`scratch/b-gen9-target-identity.log` 里
  `test_an_explicit_release_only_honours_its_own_ref` 是 `FAIL`（`2 != 1`），原因见 result §7.3
  （断言早于 `situations` 台账，属测试陈旧，非产品回归）；修法只改测试断言，产品语义未放宽。
* `tests/test_day_life_quiet_runner.py::DayLifeQuietRunnerTest` 5 个 exact IDs 全 `PASS`，`EXIT=0`
  （`scratch/b-gen3-new-quiet-runner.log`）。
* 直接受影响的既有链：`tests/test_subject_reading_cycle.py::SubjectReadingCycleTest` 9 个 ID 全 `PASS`，
  `EXIT=0`（`scratch/b-gen3-affected-reading-cycle.log`）。按父级要求，本轮**没有**重刷 16/5/9 整组。
* **更正（不要冲淡）**：本包更早一轮报告的"All green"**不成立** —— 当时
  `tests/test_life_stay_early_exit.py` 的非法退出负控就是红的（`EXIT=1`），我在报告开头把它写成了全绿；
  红的证据一直保留在 `scratch/b-gen2-affected-stay-exit.log`，没有删除、没有重跑刷绿。父级真实 owner 链
  gen2 另外抓到 `_classify_body_action` 用动作前 snapshot 的 bug（`day-life-worker-first-1792332184957194160.json`、
  `day-life-parent-launch-gen2.log` 保留为红证），已修并补 1 条精准测试；gen3 又抓到本 note 第 1/4 条里的三个
  真错（红证 `day-life-worker-first/second-1792332458187633914.json`、`day-life-parent-launch-gen3.log`），
  也已修并补 4 条精准测试。
* 只跑以上 exact 组，不 discover、不继承旧大类；`PYTHONDONTWRITEBYTECODE=1` 全程开启；日志名唯一、夹具
  （`/tmp/nyairov04b-*`）保留不删；没有 `py_compile`、没有全树 SHA、没有 commit/push/deploy、没有真实模型/
  密钥/实例。**这是机制验收，不是 24h 运行证据，也不等于"真实自主"。**
