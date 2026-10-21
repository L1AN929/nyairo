# v0.4 阶段 A 结果：自主日常主循环 + 未来事项 + 合法搁置/切换/恢复 + 多项目选择

> 一句话：这一阶段把「她自己安排的以后」做成了真东西 —— 说得出、存得下、到点会重新被考虑、能取消能改期；
> 遇阻可以按 owner 自己的规则搁置并去干别的，到点由 eligibility + 显式 RESUME 回到原来那份；多份作品可以
> 由 Decision 点名续哪一份；从她自己的材料/疑问/已有项目能长出新项目的**选项**，选中后由 B 的 owner 服务
> 真的建出来。没有真实模型、没有真实 Supply socket、没有实例运行。

## 1. 实现了什么

| 能力 | 落点 | 关键事实 |
| --- | --- | --- |
| 「稍后事项」域 | `components/life/scripts/life_future_items.py`（新，A 独占） | `fitem:<hex>` + 标题 ≤200 + 她自己定的到期 + 来源 refs + `OPEN/CANCELLED/DONE`；`life_future_items.json` 原子写（temp + `os.replace` + fsync），不 import `fcntl`；延迟 1 分钟～14 天越界按名拒绝；同一意图幂等 |
| 模型提出并真的保存 | `agency_decision_ag1.py`（`later_item` / `future_item_actions` 两个可选键 + `DecisionRecord.later_item_proposal` / `.future_item_actions`）；`autonomous_life_cycle._apply_future_item_writes` | 校验用 domain 唯一形状（`validate_proposal` / `validate_actions`）；**写**发生在 Decision 提交之后，逐条报 `APPLIED` / 拒绝原因；延迟按本轮 `observed_at` 解析 |
| 到期重考 + 取消/改期 | `life_future_items.FutureItems.due/reschedule/cancel`、`apply_actions`，cycle `due_future_items` / `project_options` | due 只回答「时钟走到哪几条」，不执行；取消/改期各一次 owner 写；due 集合进处境指纹，所以同一集合安静、集合变化重新问 |
| 合法搁置 / 切换 / 恢复 | `life_future_items.park_wait_spec`（`TIME_AT_OR_AFTER` + `time:<epoch 秒>` + owner 自己 `schedule_agenda` + `release_foreground=True`） | 前台由 **owner 的标志**释放，不绕单前台；到期由 LR-4 `scan_due_agenda` → eligibility → RESUME 候选 → `explicit_resume_from_eligibility`，没有任何自动恢复。`waiting_on_ref` 必须是结构化 ref（`<namespace>:<id>`），所以那一瞬在 ref 里写成 epoch 秒，ISO 仍放 `parameters.target_time`（LR-4 自己读的键） —— 写成 `time:<ISO>` 会被 `ResumeCondition` 直接拒 |
| 多项目并行 | `cycle.round_point_for` / `resume_point_for_artifact` / `_work_adoption_spec_for_decision` / `_work_step_spec_for_decision`，`_RoutedAdoptionSpec` 增 `project` 分支 | adoption 与 step 都跟着 Decision 点名的那份走；点名一份 owner 已不再 resumable 的作品 → 具名 `WORK_NOT_CONTINUABLE`，不悄悄回落 |
| 新项目选项 + owner 创建 | `components/life/scripts/life_project_options.py`（新，A 独占）+ `cycle._project_adoption_spec` / `_project_step_spec` | 选项只来自她自己的 `unclear` 问题 / due item / 已有项目（同标题不重复提）；没有 owner 就是 `PRIVATE_PROJECT_OWNER_UNAVAILABLE`；选择一项 = adoption 里那一次 owner 写，确认后新建的作品立刻就是普通的可续写作品 |
| 候选通道 | `artifact_continuation_source.py` 增只读 `extra_opportunity_provider` | `fitem:` / `popt:` 走**既有** `PERSONAL_OPPORTUNITY` 来源，不新增 source_kind（禁列里正是 `PRIVATE_PROJECT` 这类名字）；`refresh` 时按「这一轮还提不提供」失效 |
| 安静 | `life_step_readiness.SITUATION_KEYS` 增 `due_items` / `project_options` | 到期集合 / 可选项集合变化 = 新处境；不是 tick、不是配额、不是任务库 |
| 生产接线 | `autonomous_life_assembly.assemble_autonomous_life` | `FutureItems(root)` + `lpo.resolve_gateway(environment=env, ...)` + 从私人事项文档读 `unclear` 的 questions provider，全部可缺省；缺了就是「没有」，不造假 |
| 模型真的看得见 | `agency_cognition_adapter.py`：`SYSTEM_PROMPT` 说明 `later_item` / `park_current` / `future_item_actions` 的形状与含义；`_compact_candidate` 保留 `source_ref` / `target_refs` / `legal_decisions` / `executable_now`；`build_prompt` 接入 `cycle.decision_situation_context`（有界私文，不进运行日志） | 之前能力存在但**模型看不到**：提示词不提这两个键，候选里也读不到「这是哪份材料/能不能现在做」。现在 `later_item`（含 `park_current`）、到期项、项目选项、正在读的东西、她自己写的问题、能否 park 都在场，且只是一份 now 的处境，不授予任何权力 |

`budget / pause / 幂等` 全部保持原样：`evaluate_once` 的 pause 门、请求预算、`rounds_run` / `budget.spend()`
没有改动一行；no-op 与 quiet 的语义也没变。

## 2. 能怎么运行

```bash
# 新增阶段测试（POSIX；life 模块 import fcntl）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_v04_autonomous_day_stage

# 直接受影响的既有链（本次只跑这三组，不做全库回归）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_day_life_decision_legality tests.test_life_wait_resume_v04 tests.test_life_closeout_cycle_v04

# 真实 owner 链（真实 Supply socket + 真实 AG-1/AG-2；决策与内容两个模型调用是脚本替身）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.test_v04_autonomous_day_stage.StageARealOwnerChainTest
```

真实运行仍走既有入口（`autonomous_life_runner` / resident / `one_day_life`），不需要新命令：装配时
`future_items` / `project_owner` / `project_questions_provider` 由 `assemble_autonomous_life` 自动接上。
`project_owner` 是否可用取决于 B 的 `private_project_service` 工厂需要的环境（Supply socket / subject /
workspace / grant）；不全就是「不可用」，此时**不会**出现任何项目选项。

## 3. 已测什么（实跑，未做全库回归）

| 组 | 条数 | 结果 | 日志 |
| --- | --- | --- | --- |
| `tests/test_v04_autonomous_day_stage.py`（离线那组：`FutureItemsStoreTest` / `ParkSpecTest` / `ProjectOptionsTest` / `StageADayChainTest`） | 16 | `OK`，`rc=0`（`Ran 16 tests in 2.351s`） | `/tmp/stageA-offline-final.log` |
| `test_day_life_decision_legality` + `test_life_wait_resume_v04` + `test_life_closeout_cycle_v04`（直接受影响） | 43 | `OK`，`AFFECTED_EXIT=0` | `/tmp/stageA-affected.log` |
| `StageARealOwnerChainTest`（同一文件内，真实 owner 链） | 2 | `OK`，`rc=0`（`Ran 2 tests in 6.713s`） | `/tmp/stageA-final.log` |
| `tests.test_v04_autonomous_day_stage`（整个文件：16 离线 + 2 真实 owner 链） | 18 | `OK`，`rc=0`（`Ran 18 tests in 6.396s`） | `/tmp/stageA-module-final.log` |

新测试里的一条是**完整功能链**（离线决策替身，非真实模型）：

1. 一轮只 `DEFER`，同时提出「30 分钟后把这个疑问写成一个东西」→ 文件里真的多了一条 `OPEN`，`due_at`
   就是她说的那一刻；
2. 到点：`due()` 给出这一条，并把它变成**有界项目选项**（`basis_ref` = `fitem:…`，`seed_text` = 她的标题）；
3. 她选它：B 形状的 owner 真的被调用（`title` / `seed_text` / `source_refs` / `operation_key=` `project:` /
   `workspace_id`），返回的作品 id 成为 Activity 的 target，随后步骤 `payload_spec.artifact_id` 就是**新作品**；
4. 她取消：item 变 `CANCELLED`，之后不再 due。

另外覆盖：同一意图两次 → 一条；改期 → `due_at` 变、仍 `OPEN`；越界/两种时间同时给 → 按名拒绝；action
只到 `fitem:` 名字空间；park spec 是 `time:` 方言的 LR-4 条件且 `release_foreground=True`；项目选项只来自
真材料、同标题不重复；无 owner 时按名拒绝；两份作品时 Decision 点名第二份就续第二份；点名不可续的作品 →
`WORK_NOT_CONTINUABLE`；无事可做 → `NO_MATERIAL` / `llm_calls=0` / 协调器根本没被调用；新到期项改变处境指纹；
A 的 `resolve_gateway` 能直接接上 B 的 `private_project_service` 工厂（环境不全时按名不可用）。

`StageARealOwnerChainTest` 的两条是**真实 owner 链**：真实 `LifeSupplyService`（自己的 Unix socket、治理 + 艺术品库）、
真实 AG-0 候选集、真实 AG-1 硬约束与决策校验、真实 AG-2 adoption、真实 LR-2/LR-4 命令、真实 AR-0 写入；
只有「决策怎么答」和「内容怎么写」两个模型调用是脚本替身（不读凭据、不调真实模型、不写运行日志）。

1. **搁置 → 干别的 → 到点 → 显式回来。** 起手 `START` 让 seed 跑起来 → 下一轮 `WAIT` + `later_item{park_current:true,
   in_minutes:2}`：活动变 `WAITING`，`foreground_activity_ref=None`（**owner 自己**放的前台），条件
   `TIME_AT_OR_AFTER` + `waiting_on_ref=time:<epoch>`，agenda `TIME_AT` 同一瞬间，`future_items` 一条
   `PARK_CURRENT_ACTIVITY` 指向被 park 的活动；**parked 期间**，一条她自己到期的 `fitem:` 变成项目选项并被选中
   → B 的真实 Supply 建出新作品、本轮真的写它一版，而原活动仍是 `WAITING`、seed 一个字节没动；到点
   `maintain_waiting` 只产出 **1 条 OPEN eligibility**、没有自动恢复；再把新的那份也放下（第二次 park，前台再空）
   → 然后 `RESUME`（AG-1 校验、AG-2 applied）→ 原活动回 `ACTIVE`、重新持有前台，seed 版本恰好 +1。
2. **疑问/到期的 → 新项目 → 续第二份。** `DEFER` + `later_item`（存得下）→ 到点成为唯一的有界项目选项
   （`basis_ref` = 那条 `fitem:`）→ 选中即 B owner 真实 `create_project`（`title`/`seed_text`/来源 refs/`operation_key`）
   → 新作品 v1 = 她的标题文本，`step_work_target=artifact:<新作品>`，本轮步骤写到**新作品** v2（内容含替身生成），
   同时 seed 仍停在 v1（`_routed_step_builder` 末尾统一委派 `_work_step_spec_for_decision`，步骤只跟着
   Decision 点名的那份走，不静默回落）。

## 4. 与 B 的对接（已读 B 的 result，已按它对齐）

* B 的 `LifeProjectOwner.create_project(*, title, seed_text, content, source_refs, operation_key,
  workspace_id, subject_id)` 与 A 的 `ProjectOwnerGateway.create_project(...)` 传的参数**逐个对上**；
  `list_projects()` 返回的 `{"status": "OK", "projects": [...]}` 也被 A 的 gateway 接受。
* B 的四个工厂名（`private_projects_from_env` 等）正是 A 的 `resolve_gateway` 依次查找的名字，所以
  A 侧代码**一行都不用改**就接得上（本次已用 `resolve_gateway(private_project_service, environment={})`
  验证：接得上，且环境不全时答 `PRIVATE_PROJECT_OWNER_UNAVAILABLE`）。
* A 侧 `life_project_options._artifact_id_of` 已改为**原样保留 owner 的 `artifact:` 拼写**（不再剥前缀），
  与 B 的「裸十六进制 id 会被规范回 `artifact:<hex>`」两个方向都兼容；A 内部构造 Activity target 时再剥一次。
* **B 在 §1.4 请求的 `LifeArtifactPort.list_versions` 本次没有实现**，理由是要先有 owner 侧的 op：B 自己也
  写了「缺的只是『按 artifact 列版本』的 op」。A 现在加一个 port 方法只会得到一个 owner 必然拒绝的调用，
  等于造一个假能力。**建议由父级决定**：要么在 Supply 侧加 `list_versions` op 后 A 立刻补这一行（A 的
  `READ_OPS` + 一次映射，约 15 行），要么让 B 的 `history.complete` 保持 `False`（现状，已如实报告）。

## 5. 剩余具体问题（不遮）

1. **真实模型没跑。** 两个模型调用（决策、内容）是脚本替身：决策替身只回一份**原始 JSON**，交由真实
   `DecisionOutputValidator.validate_decision_output` + `DecisionRecord` 校验，所以 `later_item` /
   `park_current` / `popt:` 选择这一跳现在**真的**过了 AG-1（不再是只测 domain）；但真实模型会不会**想到**
   用这两个键，只能由父级用真实产品模型跑一轮才知道。提示词与处境已按此改写（见 §1「模型真的看得见」）。
2. **项目创建在 adoption 内。** 若 owner 建成功而 AG-2 随后拒绝 adoption，会留下一个**真实**作品；本轮如实
   报 `project_created` + `adoption_status`，不谎报，但没有自动回滚（回滚就等于删她的东西，不做）。
3. **真实 owner 链只跑在测试进程里。** 真实 Supply socket + 真实 AG-1/AG-2 已在
   `StageARealOwnerChainTest` 跑通两条，但**没有**跑真实 instance、也没接真实产品模型 —— 那一步留给父级。
4. **第二次 park 是测试里的合法动作，不是系统行为。** 一个活动占着前台时 RESUME 另一份会被 owner 自己拒
   （`foreground_conflict`，只有前台是 `WAITING` 才允许后台恢复），所以「回来」之前必须先把手上那份放下；
   本轮没有为「自动把当前那份让开」发明任何捷径。真实模型能不能把这条顺序想对，同样要等真实模型跑一轮。
5. **due item 无对象时不作为候选。** 它只作为项目选项出现（避免建出「无事可做的活动」）；如果将来希望它也能
   以「就想想」的形式被选中，需要一个新的动作语义，本次没有发明。
6. **安静口径**：到期集合与可选项集合进处境指纹。取消/改期后集合变化会重新问一次（有意），因此
   「改期 → 立刻再问」是预期行为，不是抖动。
7. **`life_project_options.MAX_OPTIONS = 2` 与 cycle 的 `max_project_options`** 是本次取的界；多出来的源
   直接不出现（不排队、不轮转）。

## 6. 停写

本阶段 A 到此停写：没有物理删除、没有清理、没有 `git reset/clean`、没有发布、没有动 B 的新模块与 Phone
文件、没有改模型配置、没有读凭据、没有调真实模型。全树 dirty 与全部 fixture（`/tmp/nyairo-v04-stage-a-fixtures`、
`/tmp/stageA-*.log`）原样保留。
