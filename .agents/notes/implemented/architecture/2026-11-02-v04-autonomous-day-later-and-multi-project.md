# Agent Note: v0.4 阶段 A —— 自主日常的「稍后」、搁置/切换/恢复、多项目选择

Status: implemented

## Problem

0.4 这一阶段要求主体「正常过一天」，但当时的日循环只能考虑**已经在场**的东西：前台的 Activity、可续写的
作品、当前材料的一页、看到的去处。由此有三个真实缺口，全部出现在「她本来可以自己安排生活」的地方：

1. **「回头再做」说了就没了。** 她可以在短理由里写「回头把这段改一遍」，但没有任何地方能存下这件事、记住
   它是几点、到期时重新拿出来考虑，也没有取消/改期的路。日循环里唯一能表达「以后」的只有 WAIT，而 WAIT
   的依赖必须是一条**别人欠她的**具体事实（`life_wait_basis` 的两个来源），她自己排的时间没有位置。
2. **遇阻只能停在原地。** 前台 Activity 没有下一步时，合法的出口是 PAUSE（但 PAUSED 仍然占着前台，见
   `activity_continuity.py:2290-2293`）或 WAIT（`waiting_spec` 里 `release_foreground=False`，前台不释放）。
   两条路都让前台继续被占着，于是「搁置一下、先做别的、回头再接着做」整体不成立：既起不了新事，也没有
   恢复的入口（RESUME 法定条件是 LR-4 的 eligibility，而 eligibility 只由 WAITING 的 condition 产生）。
3. **只能续写被绑定的那一份。** `_read_resume_point` 选一份，adoption spec 与 step spec 都按那一份建；
   就算 AG-0 同时给出了两份 ACTIVE 作品的候选，Decision 选了第二份，Activity 与步骤仍会落到第一份上
   （`_RoutedAdoptionSpec` 只区分 world/reading/closeout）。因此「多个私人项目并行推进」不成立，
   她永远回到启动时绑的那一个。

## Decision

一句话：**给「稍后」一个自己的域，把「搁置」接到 owner 已有的 release/eligibility 机制上，让步骤跟着
Decision 点名的那份作品走，并新增一个窄的、可拒绝的项目 owner 接缝。**

1. **新的「稍后事项」域（A 独占）**：`components/life/scripts/life_future_items.py`。一条 item 是
   `fitem:<hex>` + 标题（≤200）+ 她自己定的到期时间 + 来源 refs + 状态（OPEN/CANCELLED/DONE）+ 考虑次数。
   存在 cycle 自己的 state root（`life_future_items.json`，临时文件 + `os.replace` + fsync，不 import
   `fcntl`）。`due(now)` 只回答「时钟走到哪几条」，**不执行任何事**；取消/改期各是一次 owner 写；重复提交
   同一意图幂等（键 = 标题+到期+refs），不同意图就是第二条。延迟上下界 1 分钟～14 天，越界按名拒绝。
   domain 自己定义 proposal/action 的**唯一形状**（`validate_proposal` / `validate_actions`），AG-1 借用它，
   不复制第二份定义。
2. **模型真的能提出并保存**：AG-1 的 decision output 增加两个**可选**键 `later_item` /
   `future_item_actions`（`agency_decision_ag1.py` 的 `allowed_top_keys` + 校验 + `DecisionRecord` 的两个
   字段 `later_item_proposal` / `future_item_actions`）。它们只是「她说了什么」的投影；**写**由日循环在
   Decision 提交后做（`_apply_future_item_writes`），逐条报告 `APPLIED`/拒绝原因。提案里她给的是**延迟**
   时，按本轮 `observed_at` 解析，重放同一轮不会漂移。
3. **到期 → 候选**：due item 走既有的 `PERSONAL_OPPORTUNITY` 来源（不新增 source_kind —— 禁列表里正是
   `PRIVATE_PROJECT` 这类名字）。`ArtifactContinuationSource` 增一个只读的 `extra_opportunity_provider`，
   同一轮里发出 `fitem:` / `popt:` 候选，并在 `refresh` 时按「这一轮还提不提供」失效。
   **没有对象的 due item 不作为候选**（否则会建出一个无事可做的 Activity），它作为**新项目选项**出现。
4. **搁置/切换**：`life_future_items.park_wait_spec` 给出 `TIME_AT_OR_AFTER` + `time:<epoch 秒>` 的 LR-4 条件、
   由 owner 自己 `schedule_agenda`，并 `release_foreground=True` —— 前台由 **owner 自己的标志**释放，所以
   「先做别的」是 owner 允许的，不是循环绕开单前台。到期由 LR-4 的 `scan_due_agenda` 产出 eligibility，
   再由 RESUME 候选 + 显式 RESUME 走回来（`explicit_resume_from_eligibility`），没有任何自动恢复。
   `waiting_on_ref` 必须是结构化 ref（`<namespace>:<id>`），所以那一瞬在 ref 里写成 **epoch 秒**，ISO 仍放在
   `parameters.target_time`（`ResumeConditionEvaluator` 读的键）—— 写成 `time:<ISO>` 会被 LR-4 直接拒。
5. **多项目**：adoption spec 与 step spec 都改成「按 Decision 点名的那份」建
   （`_work_adoption_spec_for_decision` / `_work_step_spec_for_decision` / `round_point_for`），
   `resume_point_for_artifact` 先读来源本轮的 resume 缓存，再直读 port，并**用 owner 的拼写**（裸 id 在 owner
   那里是 NOT_FOUND）。点名了一份 owner 已不再 resumable 的作品 → 具名拒绝 `WORK_NOT_CONTINUABLE`，绝不
   悄悄回落到另一份。
6. **新项目 owner 接缝（B 的服务）**：`life_project_options.py` 只做两件事：由她自己的材料（`unclear`
   问题）、due item、已有项目**生成有界的选项**（同标题不重复提、没材料不提），以及一个只读/一次写的
   gateway。没有 owner 就是 `PRIVATE_PROJECT_OWNER_UNAVAILABLE`，不建任何替代作品库。选择一项 = adoption
   规格里的那一次 owner 写（`_project_adoption_spec`），确认后新作品就是一件普通的可续写作品，步骤立刻
   指向它。
7. **安静仍然是安静**：`life_step_readiness.SITUATION_KEYS` 增 `due_items` / `project_options`。到期集合
   或可选项集合变化 = 新处境（重新问）；同一集合保持安静。不是 tick、不是配额。
8. **能力必须让模型看得见**：`agency_cognition_adapter.py` 的 `SYSTEM_PROMPT` 现在说明 `later_item` /
   `park_current` / `future_item_actions` 的形状与用途；`_compact_candidate` 保留 `source_ref` /
   `target_refs` / `legal_decisions` / `executable_now` / 有界描述；`build_prompt` 把
   `cycle.decision_situation_context` 作为 `situation_context` 交给适配器（有界私文，**不进运行日志**），
   里面有她自己开着的「稍后」条目（可按键取消/改期）、到期项、项目选项、正在读的东西、她写下的疑问、
   能否 park 当前活动。它仍然只是一份「现在」，不授予任何权力。
9. **步骤只跟着 Decision 点名的那份**：`_routed_step_builder` 末尾统一委派
   `_work_step_spec_for_decision`，修掉了「项目轮把步骤写到启动时绑定那份」的真实缺陷（否则新建项目的
   第一版会被写到旧作品上）。

## Alternatives considered

* **把「稍后」塞进 LR-4 的 AgendaItem。** 最强立论：Agenda 已经有 due/trigger/status，复用最省。否决原因：
  AgendaItem 的 `__post_init__` 要求 `resume_condition_ref`/`waiting_episode_id`/`activity_id` 这一套绑定，
  它表达的是「某条 WAITING 的活动何时值得重估」，而「她以后想做什么」不依附于任何活动，也不该为了存一条
  想法先造一个 WAITING。硬塞会让每个念头都变成一条活动状态。
* **把「稍后」做成 Commitment / Goal。** 最强立论：已有 owner，省一个新域。否决原因：Commitment 是**对别人
  的承诺**，Goal 是**显式目标**，两者都会把「我随口记一笔」升级成有约束力的东西；这正是设计禁止的
  「自由文本自动晋升」。
* **给 AG-0 加一个新 source_kind（如 `PRIVATE_PROJECT`）。** 最强立论：语义最清楚，分类最干净。否决原因：
  `candidate_sources_ag0.py:106` 的禁列表里就有 `PRIVATE_PROJECT`，加它等于把被禁的名字合法化；而且新
  source_kind 要同时改 AG-1 legality、registry、materializer 与所有穷举校验。改用已批准的
  `PERSONAL_OPPORTUNITY` 是同一个意思、零新增类别。
* **PAUSE 时直接清 `foreground_activity_ref`。** 最强立论：一行就能「搁置后切换」。否决原因：前台归 LR-2
  所有，绕过它的 `foreground_released` 语义会让「谁在前台」出现两个真相，且 LR-2 的快照校验会直接判
  `FOREGROUND_MISMATCH`。本包只走 owner 已有的 `release_foreground` 路径。
* **没有 B 的服务时先用 LifeArtifactPort 自己建一件作品。** 最强立论：立刻能跑，不必等 B。否决原因：那会
  变成第二个作品库/第二条创建路径，B 的 owner 一落地就会出现两种「项目」。宁可具名拒绝。
* **把项目候选也做成 `fitem:` 之外的 `project:` 候选并直接建 Artifact。** 同上，重复 B 的写权。

## Consequences

* 现在可以：她说「半小时后回头做这个」→ 真的存下来（文件在 state root）→ 到点作为材料/选项重新进入考虑
  → 她可以选它（owner 真的建出作品，步骤立刻指向新作品）、也可以取消或改期（各一次 owner 写，逐条报告）。
* 现在可以：遇阻时按 owner 自己的规则把当前活动搁置（`TIME_AT_OR_AFTER` + 释放前台 + owner 排的 agenda），
  期间开始别的、以及到期后由 eligibility + 显式 RESUME 回到原来那份。
* 现在可以：同时存在两份以上作品，Decision 点名哪份就续哪份；点名一份已不可续的作品会具名拒绝。
* 已知代价与残留：
  1. `later_item` 的解析在 AG-1（校验）与 cycle（写）两处，但**形状只有一份**（`life_future_items`）。
     现在 `StageARealOwnerChainTest` 用**真实** AG-1 `DecisionOutputValidator` + `DecisionRecord` 跑过
     `later_item` / `park_current` 与 `popt:` 选择（决策替身只回原始 JSON），这一跳不再是「只测 domain」；
     仍未接真实产品模型 —— 模型会不会**想到**用这两个键，只能等真实模型跑一轮（提示词与处境已按此改写）。
  2. 项目选项只在 owner gateway 可用时出现；B 的服务一旦改变签名，A 这边按名拒绝而不是猜。
  3. 项目创建发生在 adoption 里：若 owner 建成功而 AG-2 随后拒绝 adoption，会留下一个**真实**作品（下一轮
     它就是普通的可续写作品），本轮如实报 `project_created` + `adoption_status`，不谎报。
  4. due item 若没有可执行对象，只作为项目选项出现，不会建出一个「无事可做的活动」。
  5. 到期集合进入处境指纹，因此「同一个到期集合」保持安静；取消/改期后集合变化会重新问一次，这是有意的。

## Verification

* 新增 `tests/test_v04_autonomous_day_stage.py`：离线 16 条用例（`OK`，`Ran 16 tests in 2.351s`，`rc=0`，
  日志 `/tmp/stageA-offline-final.log`）+ 2 条真实 owner 链（见下）。
  离线那组：1 条完整链路（DEFER 提案 → 真存盘 → 到期 → 变成项目选项 → 选择 → owner 创建作品 → 步骤指向
  新作品 → 取消）+ 其余覆盖幂等/改期/越界/park spec/选项来源/多作品点名/安静。
* 真实 owner 链 2 条：`python3 -m unittest -v tests.test_v04_autonomous_day_stage.StageARealOwnerChainTest`
  → `OK`，`Ran 2 tests in 6.713s`，返回码 `rc=0`（日志 `/tmp/stageA-final.log`）。真实 `LifeSupplyService` +
  真实 AG-1/AG-2/LR-2/LR-4/AR-0，只有决策与内容两个模型调用是脚本替身：
  ①`START` → `WAIT`+`park_current`（`WAITING` + 前台 `None` + `TIME_AT_OR_AFTER time:<epoch>` + `TIME_AT`
  agenda + 一条 `PARK_CURRENT_ACTIVITY`）→ parked 期间另一条到期项变成项目选项并被选中、B owner 真建新作品
  且本轮写它一版、原活动仍 `WAITING`、seed 未动 → 到点只出 1 条 OPEN eligibility、无自动恢复 → 把新的那份
  也放下 → 显式 `RESUME` → 原活动 `ACTIVE` 并重持前台、seed 版本 +1；
  ②`DEFER`+`later_item` → 到期成为唯一项目选项（`basis_ref=fitem:…`）→ B owner 真实 `create_project` →
  新作品 v2 由本轮步骤写成，seed 仍停在 v1。
* 直接受影响的既有链 43 条：`tests.test_day_life_decision_legality`、`tests.test_life_wait_resume_v04`、
  `tests.test_life_closeout_cycle_v04` 全 `PASS`（`OK`，`Ran 43 tests`）。
* 按父级要求**没有**跑全库回归、没有 SHA 巡检、没有跨进程 qualification、没有真实模型/密钥/实例；
  `PYTHONDONTWRITEBYTECODE=1` 全程开启；夹具在 `/tmp/nyairo-v04-stage-a-fixtures` 保留不删。

## 后续（v0.5，2026-10-26）：到期重新考虑走正式 runner 窗口；改期时钟对齐提案

实现事实补充，原决定不变：

* **改期的相对延迟按本回合的 `observed_at` 解析**（此前 `validate_actions` 在真实墙钟上解析
  `in_minutes`，压缩/注入业务钟下会把事项跳到墙钟时刻）。`validate_actions` 回显 `in_minutes`
  与解析出的 `due_at`，与 `later_item` 提案同一条幂等形状；真正写入由 `apply_actions(now=...)`
  按回合时钟重算。取消/改期仍是各自一次 owner 写，越界与未知字段仍按名拒绝。
* 到期项的正式窗口路径已用真 owner 冒烟串起来：`AutonomousLifeRunner.run_due` →
  `materials()`/处境指纹 → AG-0 `FUTURE_ITEM_DUE` 候选或既有项目选项 → AG-1/AG-2/AR-0；
  到期前 `QUIET`（0 请求），同一到期集合保持安静，取消/改期后不复活；无对象项只作为项目选项。
* 搁置到期的返回链与本链共用 LR-4 的 OPEN 资格：另一 ACTIVE 前台时没有 RESUME 候选、资格不被
  消费；释放前台后显式 RESUME 消费一次。供给规则见
  [有效搁置事项在安静门之前成为候选](../feature/2026-10-24-eligible-parked-candidate-supply.md)。
* 证据：`tests/test_v05_due_reconsideration.py`（5 例）与
  `scripts/v05_due_reconsideration_smoke.py`（44 项），
  说明 `docs/engineering/v05-due-reconsideration-20261026.md`。仍是脚本 Decision/内容，
  不证明真实模型自然提出或采用。

### 2026-10-26 交叉点修正：到期事项不能绕过 PAUSED/WAITING 的 RESUME

实现事实补充，原决定不变。到期项进入同一个 `PERSONAL_OPPORTUNITY` 来源的 extra 路径后，
此前没有沿用「目标已有 PAUSED/WAITING Activity 就不发新候选」这条已有规则：

* 真 owner 正式链复现：作品被真实 `PAUSE`（无条件、无资格）或 `WAIT` 后，指向它的到期事项
  仍产出 `FUTURE_ITEM_DUE` 候选、`legal_decisions` 含 `START`，AG-2 采纳后写出新版本并在
  同一作品上开出第二个 Activity。不是「owner 最终会拒」的展示问题。
* 修复只落在 `artifact_continuation_source.py` 的 `_extra_raw_items` 与 extra 的 `refresh`：
  按 owner 目标身份跳过/撤回，原因仍是 `PAUSED_TARGET_REQUIRES_RESUME`；事项本身保持
  OPEN/due，`popt:` 与没有挂起活动的可续作品照常供应。
* 证据：`tests/test_v05_due_parked_target.py`（6 例）与
  `scripts/v05_due_parked_target_smoke.py`（35 项，修前 8 红、修后 `SMOKE_OK`），
  说明 [交叉点说明](../../../../docs/engineering/v05-due-parked-target-20261026.md)。
  Decision/内容仍是脚本替身；不证明自然采用、G1、V-C 或 24h。
