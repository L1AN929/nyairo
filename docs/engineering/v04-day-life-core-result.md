# v0.4 一天生活 B：核心连续性（步骤就绪 / 具名无步骤 / 真实退出核对 / 有限再考虑）结果

Status: implemented（机制层）。**本文不声称"她能正常活一天"**：这是有限机制验收，不是 24h
运行证据，也不是"真实自主"的证明。真实 24h 需要 provider 授权、活着的 Supply/World owner、
显式 `--duration-seconds 86400` 与请求上限，另列。

## 1. 结论（先说能做什么、不能做什么）

这次修的是"一天里真正卡住她的四件事"，全部落在 B 的独占写集内：

| # | 之前 | 现在 |
|---|---|---|
| 1 | 材料读尽/撤权/`no_change` 之后前台仍 `ACTIVE`，CONTINUE 仍合法但无步骤可跑 | cycle 产出**有来源、只绑定当前 Activity/target/revision** 的只读 step readiness，向 AG-1 事实通道给 `current_step_unavailable_true`（仅 ASCII 布尔键、值就是 `True`）；A 据此撤掉当前 CONTINUE，PAUSE/ABANDON/DEFER/NO_ACTION 全保留 |
| 2 | 无步骤却报 `ACTED`，或把"做不到"写成 `NO_ACTION` | 具名 `NO_STEP_AVAILABLE`（unavailable/blocked）；runner 归类 `blocked`，绝不记成她的选择，也不记成动作 |
| 3 | 通用 PAUSE/ABANDON 在核对 adoption 之前就报"已停" | 先看 Adoption 是否 `APPLIED`，再回读 Activity owner 自己的状态；被拒 → `ADOPTION_REJECTED`，应用了却仍 `ACTIVE` → `STEP_UNSETTLED`。**拒绝不会报成已停** |
| 4 | 同一状态每轮再花一次请求 | 有限（≤8 条/种）、持久、有寿命（6h）、可解除的再考虑台账，按**整个窄处境的指纹**判定：前台 Activity 及其状态/类型、步骤 target 与**工件** revision、阅读状态/revision/下一页、作品 head、所在地/姿态/朝向、**机会 target 集合**。只存 fingerprint/parts/引用/时间/计数，**不存正文、不存理由、不存 Decision**；真实新页面/新 head/新机会/新前台自动重开；显式解除只认自己的 ref |

另外两件已接线但机制有限的事：

* **PAUSE 之后**：按现行 LR-2，PAUSED 仍保留前台占用，不能据此开始别的对象；WAITING 是另一条有条件的 owner 路径，不能混同。旧目标的停点与 resume 资格归既有 owner（LR-2/LR-4）。
  个人机会源在旧目标有 PAUSED/WAITING Activity 时不再为它发新候选（`PAUSED_TARGET_REQUIRES_RESUME`），
  防止通过新建第二个 Activity 绕过 resume 资格。本批没有解决通用 PAUSED 恢复限制。
* **W 的 `bodyact:`**：cycle 只做识别与复用 —— `_chosen_target_ref` / `_candidate_is_world` /
  `_world_step_spec` / `_world_adoption_spec` / `_classify_body_action` 全部调用 W 的
  `life_body_action_targets`（`parse_body_action_target` / `build_body_action_step` /
  `body_action_observed_fact`）。POSE 的完成判据是**真实 snapshot 的 pose+orientation**；REST 只看
  真实**会话状态**（`get_available_actions` 的 REST 项 `current.recovery_state`），命令成功不算完成，
  也不推导"她恢复了"。

## 2. 接口（对外的全部形状）

### 2.1 新模块 `components/life/scripts/life_step_readiness.py`

```python
STEP_UNAVAILABLE_FACT_KEY = "current_step_unavailable_true"   # == ag1.OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE

assess_step_readiness(*, activity, target_ref, source_revision, reading_state=None,
                      no_change=None, world_step_enabled=False, caller_can_act=True) -> StepReadiness
#   available 三态：True 有步骤 / False 确实没有（reason_code 具名）/ None 不知道
#   .bound     = 同时拿到 activity_ref 与 target_ref 才算可陈述的事实
#   .fact_keys() -> {current_step_unavailable_true: True} 仅当 available is False 且 bound

namespace_of(ref) -> "stay:"|"place:"|"object:"|"bodyact:"|"artifact:"|None   # 先剥 world_fact: 包装
merge_priority_facts(base, priority, *, max_keys=24) -> dict    # 24 键预算内，关键事实优先
ReconsiderationLedger(state_root, *, ttl_seconds=6h, max_entries=8)
    .record(*, readiness, outcome, at) / .release(*, refs) / .no_change(target, revision)
    .block(*, readiness, at, other_opportunities) -> {"blocked", "reason_code", "fingerprint", "entry"}
```

台账文件：`<life state root>/life_reconsideration.json`（与 step journal、continuity watermark 同一
写域，无第二个 store）。字段只有 `fingerprint/target_ref/source_revision/reason_code/outcome/
activity_ref/first_at/last_at/count`。

### 2.2 `autonomous_life_cycle.py`（新增/改变的行为）

* 新 outcome：`OUTCOME_NO_STEP_AVAILABLE`、`OUTCOME_QUIET`。
* 新只读方法：`open_activities()`、`step_readiness(act=...)`、`reconsideration_block(...)`、
  `release_reconsideration(refs=...)`；`status()` 增 `step_readiness` / `reconsideration` 两个投影。
* 每轮在 `has_material` 之后、`budget.spend()` **之前**做一次 readiness；命中抑制则 `QUIET`（不花钱、
  不花轮）。
* `observed_facts` 里用 `merge_priority_facts` 把 readiness 事实放在最前（24 键预算内，不会被地点事实挤掉）。
* PAUSE/WAIT/ABANDON 统一走 `_report_chosen_exit`（采纳 + owner 状态双核对）。
* 作品 `no_change` 现在落台账并绑定版本；下一轮读回 `WORK_NO_CHANGE_CONFIRMED`。
* `bodyact:` 三个分派点 + `_classify_body_action` + `_record_world_body_progress`（LR-5 原
  `WORLD_FACT_MATCH` / `WORLD_ACTION_BOUNDED` / `TARGET_STATE`，无新 policy 版本）。
* `_owner_evidence(base, outcome)`：`owner_version_created` 只有 owner 真的回了版本/checkpoint/收据才是
  `True`；可证明没写 owner 的结局是 `False`；可能写了却不知道（`STEP_UNKNOWN`/`STEP_UNSETTLED`）是 `None`。

### 2.3 `autonomous_life_runner.py`

* `OUTCOME_CLASS` 增 `NO_STEP_AVAILABLE → (WAKE_NO_STEP, "blocked")`、`QUIET → (WAKE_QUIET, "silence")`。
* `wake.extra` 透传给 R：`owner_version_created` / `owner_version_id` / `step_path`（无证据就是 `None`），
  并附带本轮的 `step_readiness` / `reconsideration` 投影。
* `QUIET` 与 `NOT_DUE` 都不计入 `wakes_run`（考虑过什么才算 wake）。
* 日志窄修：连续 `NOT_DUE`/`QUIET` 只写第一条，状态一变再写（`last_wake` 永远是最新）。
* 事件解除抑制：只有 `KNOWN_EVENT_KINDS` 且事件 ref 命中台账里的 `target_ref`/`activity_ref` 才解除，
  解除结果写进 `extra.reconsideration_released`。

### 2.4 `artifact_continuation_source.py`

* 新增可选 `paused_target_provider`；命中即不发候选、`refresh` 时 withdraw，原因
  `PAUSED_TARGET_REQUIRES_RESUME`（`last_paused_skip_reasons` 可读）。未接线时行为逐字节不变。

## 3. 测试（新 exact IDs，实测退出码）

全部在 WSL（POSIX）下跑，`PYTHONDONTWRITEBYTECODE=1`；日志名唯一，夹具保留不删。

| 文件 / 类 | exact IDs | 结果 |
|---|---|---|
| `tests/test_day_life_continuity.py::DayLifeContinuityTest` | 19（原 15 + 本轮 4） | **PASS**：原 15 见 `scratch/b-gen3-new-continuity.log`；本轮 4 条新增 + 4 条确实受影响见 `scratch/b-gen8-parentfix.log`（8 IDs，**EXIT=0**） |
| `tests/test_day_life_quiet_runner.py::DayLifeQuietRunnerTest` | 5 | **PASS，EXIT=0**（`scratch/b-gen3-new-quiet-runner.log`） |
| 直接受影响：`tests/test_subject_reading_cycle.py::SubjectReadingCycleTest` | 9（继承既有） | **PASS，EXIT=0**（`scratch/b-gen3-affected-reading-cycle.log`） |
| 直接受影响：`tests/test_life_stay_early_exit.py::StayEarlyExitTest` | 1（原先红的那条，单跑 exact ID） | **PASS，EXIT=0**（`scratch/b-gen4-affected-stay-exit-illegal.log`；另一条正向 ID 本轮未重跑，仍见 `scratch/b-gen2-affected-stay-exit.log`；归因与修法见 §5.1） |

15 条 continuity ID：

1. `test_an_exhausted_reading_material_publishes_the_bound_step_unavailable_fact`
2. `test_a_page_left_publishes_no_step_unavailable_fact`
3. `test_no_foreground_activity_publishes_no_fact`
4. `test_a_continue_with_no_step_is_named_no_step_available_and_not_acted`
5. `test_a_refused_pause_is_reported_as_adoption_rejected_not_paused`
6. `test_an_applied_exit_that_the_owner_still_reports_active_is_unsettled`
7. `test_an_applied_pause_that_the_owner_reports_paused_is_reported_paused`
8. `test_the_same_no_step_state_is_considered_once_and_then_quiet`
9. `test_a_new_material_revision_lifts_the_suppression`
10. `test_an_explicit_release_only_honours_its_own_ref`
11. `test_a_work_no_change_is_persistent_and_bound_to_its_revision`
12. `test_a_body_pose_step_is_built_by_w_and_completed_only_by_the_snapshot`
13. `test_a_rest_step_is_judged_by_the_session_state_and_never_by_recovery`
14. `test_a_body_act_adoption_names_w_fact_key_and_expected_value`
15. `test_a_paused_target_is_not_offered_as_a_fresh_personal_opportunity`

5 条 runner ID：

1. `test_a_no_step_outcome_is_blocked_and_never_acted`
2. `test_a_quiet_round_is_silence_and_does_not_count_as_a_wake`
3. `test_the_owner_evidence_is_passed_through_and_never_invented`
4. `test_repeated_not_due_polls_do_not_fill_the_wake_journal`
5. `test_an_event_about_the_quiet_object_releases_the_suppression`

## 4. 明确没做（避免被读成"已经好了"）

* 没有跑真实 24h、没有真实模型/密钥/config、没有起常驻、没有外发/支付/发布/commit。
* 没有提高任何轮数/请求上限来"掩盖循环"；pause/lease/三层预算/UNKNOWN 原键守卫全部保留。
* 没有新增 LifeManager、人格库、强制日程、第二前台、新 schema/version；没有自动完成读尽活动，
  也没有自动停活动。
* 再考虑抑制按**整个窄处境**判定，因此"没有前台但状态完全一样"也会安静下来；但它只覆盖"她已经考虑过并
  没动"的状态：真实新页面/新 head/新前台/新机会 target/新所在地或姿态都会重开，不靠隐藏世界或禁 owner 实现。
  世界 revision 单独变化**不**算新处境（tick 不是生活事件）；工件 head 变化算。

## 5. 具名残留（不是"看起来好了"）

1. `tests/test_life_stay_early_exit.py::test_an_illegal_exit_is_refused_and_a_closed_place_is_never_claimed`
   原本是红的，**根因在这个测试自己的负控构造，不在 B**：A 的 AG-1 在"前台 ACTIVE"时不再提供 START，
   那一轮真实 offer 是 6 条 `DEFER`-only 候选 + 1 条 `CONTINUE_CURRENT`（`DEFER/CONTINUE/PAUSE/ABANDON`），
   而 `IllegalAbandonCognition._choose` 只认带 `START` 的候选，于是落到兜底分支
   （`short_rationale="只有这一条候选可用"`）——它提交的是**合法的** ABANDON，owner 真的应用了，cycle 报
   `ABANDONED` 是诚实的。断言没错，是负控没成立。现已改成**确定性**地取本轮真实候选里第一条
   `legal_decisions` 既不含 `ABANDON` 也不含 `CONTINUE` 的候选（`DEFER`-only 即可）再投 `ABANDON`；
   一条都没有就 `AssertionError` 打印整份 offer，**不再兜底成合法 ABANDON**。拒绝/owner 仍 `ACTIVE`/
   前台不变/closed 无完成这几组断言逐字未动，只改负控的选候选逻辑。实测（只跑这一条 exact ID，
   `PYTHONDONTWRITEBYTECODE=1`，夹具保留不删）：**EXIT=0，Ran 1 test，OK**
   （`scratch/b-gen4-affected-stay-exit-illegal.log`）；负控那一轮 `outcome=DECISION_FAILED`、
   `detail=INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`，被拒的候选是
   `bodyact:pose:transitioning:south`（`legal=["DEFER"]`）。没有改任何产品代码。
2. `life_reconsideration.json` 的寿命是固定 6h（无 env 覆盖）；如需按实例调，需要另开一条小接口。
3. `REST` 的完成判据依赖 `get_available_actions` 里的 `REST.current.recovery_state`；如果某个 World
   版本不暴露该项，`body_action_observed_fact` 会返回 `value=None` → 本轮 `STEP_UNSETTLED`（宁可不知道，
   不报完成）。
4. 本轮的 step readiness 每轮多两次 owner 读（Activity 记录 + 该 target 的 revision）；没有缓存，
   也没有把读结果写回 owner。
5. `QUIET` 是新增 wake 状态；下游若按旧枚举穷举 `status`，需要把它当"silence"处理（`status_class` 已给出）。

## 6. 父级 gen3 短链回归（第二轮修复）

父级同实例短链（READ→ABANDON→POSE 真实回读→ABANDON→WORK no_change）已走通；随后**第二进程同状态仍
`NO_ACTION`、`llm_calls=1`**。红证 `scratch/day-life-worker-first-1792332458187633914.json`、
`scratch/day-life-worker-second-1792332458187633914.json`、`scratch/day-life-parent-launch-gen3.log` 全部保留。
定位到三个真错，均已在本写集修好：

1. **`no_change` 没有绑定真实 owner 版本。** `_readiness_revision` 把 `artifact:<hex>` 剥成裸 hex 去调
   `LifeArtifactPort`，而 owner 的 `read_artifact`/`read_head` 按 `artifact_id` **精确匹配**
   （`components/supply/lifesupply/artifact/store.py:247-272`；id 由 owner 铸成 `artifact:<uuid>`），
   于是 `NOT_FOUND` → `source_revision=None`（gen3 每轮 readiness 的 `source_revision: null` 就是这条）。
   现在按 owner 自己的拼写调 `read_head`（回退 `resume_point`），拒绝写进 `last_error`，答案仍是 `None`。
   同时 `ReconsiderationLedger.record()` 允许"只有 target + revision、还没有前台"的记录：`_work_no_change`
   在 START 采纳期抛出时用本轮**所选工作**（`_round_work_binding` = 候选 work + owner head）落盘，
   `work_no_change.recorded_target_ref` / `recorded_source_revision` 可核对；下一轮前台出现时 readiness 读回
   `WORK_NO_CHANGE_CONFIRMED` + 事实键，**保留一次合法退出考虑**。
2. **目标归属借了别的 ref。** gen3 的 POSE Activity `subject_refs` 是整组观察目标（第一个是
   `world_fact:place:street_1`），旧实现取"第一个命名空间已知的 ref"，于是 readiness 把**地点**当成它的步骤
   目标。现在从 Activity 自己的 lineage（`source_refs` 里的 `cand:`）取**该 Activity 被采纳时的那个候选**的
   target；读不到候选且只剩唯一已知 ref 才用它；多个 ref 且无法判定 → `None`（不知道），绝不借用。
   阅读 Activity 例外地优先 `reading_artifact_id`（那是本 cycle 自己的绑定对象）。
3. **"有世界机会"恒真，安静永远不成立。** 旧 `block()` 只要 `other_opportunities` 非空就
   `OTHER_OPPORTUNITY_AVAILABLE`，而世界机会几乎每轮都在，于是同一状态永远不被抑制。现在判定改为**整个窄处境
   的指纹**（`life_step_readiness.situation_fingerprint`）：前台 ref/状态/类型、步骤 target、**工件** revision、
   阅读状态/revision/下一页、作品 head、所在地/姿态/朝向、机会 target 集合。机会"存在"不再是否决理由，
   机会**集合变化**才是新处境；世界 revision 单独跳动不重开（tick 不是生活事件）。

### 6.1 本轮实测（只跑新增与确实受影响，不重刷 16/5/9 整组）

| exact ID | 覆盖 | 结果 |
|---|---|---|
| `test_a_no_change_on_a_new_start_is_recorded_against_the_real_owner_head` | 真实 owner 拼写 + START 期 no_change 绑定 | PASS |
| `test_a_body_action_readiness_target_comes_from_its_own_candidate_not_the_first_subject_ref` | POSE 目标归属 + 无候选时不借地点 | PASS |
| `test_the_same_situation_in_a_new_process_is_quiet_with_world_opportunities_visible` | 跨实例同状态零 cognition + 世界 tick 不重开 | PASS |
| `test_a_real_new_opportunity_reopens_the_question` | 机会集合真变化才重开 | PASS |
| `test_the_same_no_step_state_is_considered_once_and_then_quiet`（受影响） | 抑制改判后的 no-step 安静 | PASS |
| `test_a_new_material_revision_lifts_the_suppression`（受影响） | 新 revision 解除 | PASS |
| `test_a_work_no_change_is_persistent_and_bound_to_its_revision`（受影响） | no_change 版本绑定 | PASS |
| `test_a_body_pose_step_re_reads_the_world_after_the_action_and_never_the_target`（受影响） | 动作后 fresh 回读 | PASS |

8 个 exact IDs，`EXIT=0`，日志 `scratch/b-gen8-parentfix.log`（上一代 `b-gen6`/`b-gen7` 的失败与中间态日志
同样保留，未删除、未重跑刷绿）。

### 6.2 本轮新增的具名残留

6. `life_reconsideration.json` 现在有两类记录（`entries` 键控 target+revision、`situations` 全处境指纹），
   各自 ≤8 条；升级前写入的旧文件只有 `entries`，读时按空 `situations` 处理（不会误抑制）。
7. 世界 revision 被**有意排除**在处境指纹之外：一次没有任何可感知变化的 world tick 不会重开问题。若将来某个
   World 版本把真实变化只体现在 revision 而不体现在 place/pose/opportunity 上，需要另加一个显式感知字段，
   而不是把 revision 塞回指纹。
8. `_work_no_change` 若既无前台绑定、又拿不到 owner head（例如 port 读失败），仍**不记录**任何东西
   （`work_no_change.recorded=None` 明示）：宁可下一轮再问一次，也不凭猜测绑定版本。

## 7. 父级 gen4 短链回归（第三轮修复：target identity + 重建后仍可用）

### 7.1 症状与红证

真实短链 READ→ABANDON→WorldPOSE→ABANDON→WORK `no_change`→`NO_ACTION` 在**首进程**走通；
**新进程**对同一状态又问了一次（`llm_calls=1`，`outcome=NO_ACTION`）。
证据：`scratch/day-life-parent-gen4-fingerprint-delta.log`、
`scratch/day-life-worker-first-1792333439525094898.json`、
`scratch/day-life-worker-second-1792333439525094898.json`、
`scratch/day-life-parent-launch-gen4.log`；owner 记录只读自
`/tmp/day-life-parent-1893-1792333439525094898/instance/life/state/canonical_life_state.json`
（`actv_e59fe247900d4ed3` 的 `subject_refs=[artifact:c6bedf…, artifactversion:3092e2…]`、
`origin_ref=artifact:c6bedf…`、`source_refs` 里同时有 `cand:359904c047c62585` 与
`artifact:artifact:c6bedf…`）。

delta 只有三个字段不同，**世界机会集合完全一致**：

| 轮次 | target_ref | target_revision | step_reason | 处境指纹 |
|---|---|---|---|---|
| first 4（START 期 `no_change`） | `artifact:c6bedf…`（记录值是 `artifact:c6bedf…`） | `artifact-version:3092e2…` | `WORK_NO_CHANGE_CONFIRMED`（条目 `b01e2e46de0a48d7`） | `fe97284919d5d86c`（无前台） |
| first 5（前台 `actv_e59fe247900d4ed3`） | `artifact:artifact:c6bedf…` | `artifact-version:3092e2…` | `ARTIFACT_STEP_AVAILABLE` | `afa8ae6eac2b1da8` |
| second 0（新进程） | `null` | `null` | `NO_BOUND_TARGET` | `b1b1bd11d9f590a2` |

同一份 `life_reconsideration.json` 里同时躺着这个对象的两种拼写
（`entries` 里 `artifact:c6bedf…`、`situations` 里 `artifact:artifact:c6bedf…`）——
一个对象被当成两个 target，处境指纹自然对不上，抑制失效。

### 7.2 根因（两个，都必须修）

1. **目标归属依赖可变 candidate cache。** `_readiness_target_ref` 只从 `source_refs` 的 `cand:` 取候选，
   候选里的 target 原样返回（本树包装 `artifact:artifact:<id>`）。新进程里候选 cache 为空 → 无候选 →
   只剩 `subject_refs` 的两个「已知命名空间」ref → 判为歧义 → `None` → `NO_BOUND_TARGET`。
   于是重建后同一状态的处境指纹改变，`QUIET` 不成立。
2. **同一对象的两种拼写没有归一。** readiness 输出与落账用本树包装，START 期绑定用 owner 拼写；
   `no_change` 虽然两边都做 canonical，但 readiness 根本走不到那一步（target 已是 `None`）。

另有一条同名污染必须写清：上一轮 worker 的模型池 503 中断，把 `life_step_readiness.py` 留在半保存状态
——`record()` 里一段悬空 `else`（`... else readiness.source_revision or ""`）导致模块无法 import，
且 `namespace_of` 被定义了两次（第一份是被遮蔽的死代码）。本轮 fresh Read 后按原意图补完：
删掉被遮蔽的旧定义，`record()` 补成 `source → canonical_target_ref(source) or str(source)` + `revision`。

### 7.3 修法（写集内三个文件）

* `life_step_readiness.py`：补齐 `owner_artifact_id` / `canonical_target_ref` / `is_artifact_target`
  ——先剥 `world_fact:` 包装，`artifact:artifact:<hex>` 折成 owner 的 `artifact:<hex>`，
  `artifactversion:` 是 revision 不是 target，id 本身不掩码不截断；`record()` 按 canonical 落账；
  `no_change()` 与 `release()` 都按 canonical 比对（一种拼写落账、另一种拼写也能查回/解除）。
* `autonomous_life_cycle.py`：`_readiness_target_ref` 改为 **owner 记录优先**
  （新 `_record_artifact_refs` + `_owner_record_target_ref`）：`target_ref`→`checkpoint_ref`→`origin_ref`
  →`subject_refs`，只取工件**对象**；阅读 Activity 用 `reading_artifact_id` 消歧；工件类 Activity 只认
  单值字段；候选只作**第二**来源且必须恰好指一个可执行 target；最后才是「只剩唯一已知 ref」。
  世界侧逐字未变：没有候选的 POSE Activity 仍给 `None`（绝不借 `origin_ref` 的所在地）。
* `tests/test_day_life_continuity.py`：新增 2 条真实形状反例（§7.4），并把
  `test_an_explicit_release_only_honours_its_own_ref` 的陈旧断言改成当前两形状台账的语义（§7.5）。

### 7.4 本轮实测（只跑新增与直接关联，不重刷 8/16/5/9 整组）

全部在 WSL（POSIX）下跑，`PYTHONDONTWRITEBYTECODE=1`，日志名唯一：
`scratch/b-gen9-target-identity-fix.log`，**9 个 exact IDs 全 `PASS`，`EXIT=0`**（`Ran 9 tests … OK`）。

| exact ID | 覆盖 | 结果 |
|---|---|---|
| `test_a_work_target_survives_a_rebuild_where_the_candidate_cache_is_gone` | 真实形状（双前缀候选 + 第二进程候选 cache 为空）→ 同一状态 `QUIET`、`llm_calls=0`、世界机会仍可见 | PASS |
| `test_the_domain_wrapper_and_the_owner_id_are_one_target_across_a_rebuild` | 一种拼写落账、另一种拼写查回 + 无候选时 readiness 仍是那一个绑定事实 | PASS |
| `test_a_no_change_on_a_new_start_is_recorded_against_the_real_owner_head` | START 期 no_change 绑定（受影响） | PASS |
| `test_a_body_action_readiness_target_comes_from_its_own_candidate_not_the_first_subject_ref` | POSE 目标归属与不借地点（受影响） | PASS |
| `test_the_same_situation_in_a_new_process_is_quiet_with_world_opportunities_visible` | 跨进程安静（受影响） | PASS |
| `test_a_work_no_change_is_persistent_and_bound_to_its_revision` | no_change 版本绑定（受影响） | PASS |
| `test_a_real_new_opportunity_reopens_the_question` | 机会集合真变化才重开（受影响） | PASS |
| `test_an_explicit_release_only_honours_its_own_ref` | 显式解除只认自己 ref（受影响） | PASS |
| `test_the_same_no_step_state_is_considered_once_and_then_quiet` | no-step 安静（受影响） | PASS |

### 7.5 具名残留

9. **`test_an_explicit_release_only_honours_its_own_ref` 的断言早于 `situations` 台账。** 它原本断言
   按 ref 解除只释放 1 条，但作为抑制实际生效的那一行是 `situations` 记录，两条都带同一个
   `target_ref`，所以现在会释放 2 条（只放 1 条才意味着显式解除仍然被另一形状压着——那才是 bug）。
   红证保留：`scratch/b-gen9-target-identity.log`（`FAIL … 2 != 1`）。本轮只改测试断言并补上
   `situations` 的非空/清空断言，产品语义没有放宽。这条旧断言是 `situations` 引入后就没再进过
   回归子集的潜伏红，不是本轮产品回归。
10. **没有重跑 gen4 真实进程链。** 本轮只跑 exact IDs；父级用 scratch 复验真实短链。机制验收成立
   不等于 `24h` 运行证据，也不等于「真实自主」。
11. 每轮 readiness 仍多两次 owner 读（Activity 记录 + 该 target 的 revision）；本轮没有新增缓存，
    也没有把读结果写回 owner。候选 cache 依旧是进程内的，只是不再是目标的唯一来源。
