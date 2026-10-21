# Agent Note: v0.4 主线 A —— 她如何连续过一天（现场生活 context / prompt 结构裁剪 / 拿放路由到真实 owner）

Status: implemented

## Problem

主线 A 要回答的是"她怎么连续过一天"，而不是再多几个孤立能力。动手前实测到三个真问题：

1. **决定模型看不到现场。** `decision_situation_context` 只给研究/实践/阅读和 `place_id`；
   她站在哪个屋子（名字）、手上拿着什么、哪些地方走得到、要走多久、店开不开、这轮真能做什么，
   都不在 prompt 里。世界观测（`last_observation` / `last_catalog` / `last_opportunity_view`）
   只被用来生成候选和报告。于是模型只能对着 id 做决定，`NO_ACTION` 看起来像"她想安静"。
2. **prompt 超限时产出伪 JSON。** `build_prompt` 在 `len(text) > max_prompt_chars` 时执行
   `text[:N] + ' ...(truncated)"}'`：字符串被从中间切断、最后一条候选只剩半条记录、右括号是猜的。
   `json.loads` 必然失败 —— 这类失败会被记成模型失败，其实是被自己的 prompt 切坏的。
3. **日常拿放在主链上不可执行。** `action_intent` / AR-0 侧已经有 `PICK_UP` / `PLACE`，
   但 cycle 只把 `MOVE`/`CONSUME`/`POSE`/`REST`/`stay:` 当世界步骤；B 的 `dailyact:` 目标
   没有 step/adoption/完成核对路径，准备东西这件事在真实链上根本走不通。

## Decision

核心写集是 `agency_cognition_adapter.py`、`autonomous_life_cycle.py`、`life_step_readiness.py`
与新 `life_live_context_view.py`；完整链同时补通 AR-0 拿放准入、正式 Journey adapter 的委派和
`autonomous_life_assembly.py` 的实例环境/世界目录传递。不新增 owner、schema 或 LifeManager，
不复制 B 的 dailyact 编码，不改变 C 的 resident/runner 语义。

1. **现场生活 context（新模块 `life_live_context_view`）。** 一次读、有界、只数据：
   `location{place_id,name,kind,area_id,local_time}`、`body{pose,orientation,hands[]}`、
   `nearby[]{place_id,name,walk_seconds,availability,availability_reason,reach,observed,premises}`、
   `opportunities[]{target_ref,verb,label,description,duration_seconds,availability,preparation}`、
   `unknowns[]`。优先调用 B 已发布的契约 `provider.daily_life_context(limit=8)`；契约缺席时用
   同一份观测/目录的**已存在只读属性**做有界投影并标注 `source: provider.observation_fallback`。
   她不在的地方一律保留 owner 自己的 `observed`（`not_observed`/`OUT_OF_VIEW`），**其店内细节
   一律不复制**，并把 `OPPORTUNITY_DETAIL_NOT_READ` 记进 `unknowns`：站在街上不等于看见柜台。
   读不到的地点名/手/营业/路程各自具名（`PLACE_NAMES_UNREAD` 等），不用猜的值填。
2. **进真实 prompt。** cycle 在 `_refresh_world_opportunities` 之后（同一份观测、不再花一次
   owner 调用）读一次现场，放进 `decision_situation_context()["world"]`；adapter 的
   `_bounded_situation` 只按计数/长度再压一次（`MAX_LIVE_CONTEXT_ENTRIES=8`）。`SYSTEM_PROMPT`
   明确 `situation_context.world` 先读、逐项说明（在哪/手上/能去哪+多少秒+开不开/这轮真能做什么），
   并写明这是**连续的一天**：可以专心、顺手准备、出去再回来、结束或稍后，没有日程也没有产出配额；
   同时把旧规则补一句：`unknown` 是"没读到"，不是"没有可做的事"。
3. **prompt 容量：结构化裁剪，永不切字节。** `_fit_prompt` 先整体序列化；超限就按**声明好的顺序**
   丢弃整个字段块并重新序列化，直到放下：`prior_experiences` → 实践经历 → 研究明细 → 实践方法
   明细 → 项目选项明细 → 提问 → 候选描述缩短 → 稍后事项尾部 → 候选描述 → 理由码数量 → 阅读/活动
   字符串 → 研究块 → 实践块 → 稍后事项/项目选项 → 现场列表尾部 → 只留现场的 situation →
   **最后**才从尾部整条丢候选（`MIN_CANDIDATES_IN_PROMPT=1`，永不留半条）。candidate id /
   `legal_decisions` / `target_refs` / `legal_decision_enums` / `observed_fact_keys` 与
   `situation_context.world` 在所有步骤里都保留。裁剪报告 `last_prompt_trim` 只有
   `{trimmed, chars, limit, over_budget, dropped:[{step,count}]}` —— **没有 prompt 文本**，
   所以既能落日志也不泄露私文。
4. **拿放走完整路由。** cycle 认识 `dailyact:` 命名空间（`_candidate_is_world` /
   `_chosen_target_ref` / `_world_step_spec`）：step 形状由 B 的
   `build_daily_action_step(target, origin_ref=...)` 生成，adoption 完成契约取 B 的
   `daily_action_completion()` 的 `fact_key`/`expected_value`，activity_kind 是
   `DAILY_ACTION_IN_OBSERVED_WORLD`。执行后**重新观测一次**（`_fresh_world_body_observation`），
   先问 B 的 `daily_action_observed_fact`，B 没给就用 fresh snapshot 自己的 `hands` 结构核对
   （`PICK_UP` = 物件在某个手槽；`PLACE` = 不在手上）；一致才给 LR-5 提交
   `world_authority` + `WORLD_CANONICAL_OBSERVATION` 的进度证据，不一致/读不到一律
   `STEP_UNSETTLED` 且不写任何进度。**步骤成功 ≠ Activity 完成**：完成仍只由 LR-5 的
   `WORLD_FACT_MATCH` 判据决定。
5. **正式生产 seam 委派拿放（`world_journey_action_adapter.py`）。** 真实 run 的 resolver 就是
   `RealJourneyWorldActionAdapter(JourneyWorldResolver)`，而它的 `DELEGATED_VERBS` 只有
   `CONSUME/POSE/REST`：任何 `PICK_UP`/`PLACE` 都会在 Resolver 之前拿到
   `UNSUPPORTED_VERB_<verb>`（definite pre-submit 拒绝）。修法是把 B 的
   `life_daily_action_targets.DAILY_ACTION_VERBS` 追加进 `DELEGATED_VERBS`（**import 常量，不复制
   语义**）；`SUPPORTED_VERBS` 仍是 `("MOVE",)`，idempotency key、UNKNOWN 语义、
   `resolver.delegate.reconcile_execution` 的 reconcile 路径一行未改。
6. **`has_foreground` 的边界：只有 `WAITING` 才让出前台。** 真实链抓到的 gate：WAIT 之后 Activity 是
   `WAITING`，AG-2 已经允许新 Activity，但 cycle 把"存在前台指针"当成"还在忙"，于是 park 期间
   `world_step`/`reading_step`/`closeout_step` 全被挡掉，唯一能执行的是 **work** 步骤（实测：选中
   `dailyact:pick_up:` 却执行了一次工作成品 commit，`step_path=AR0`）。判据收窄为
   `status != "WAITING"`：`ACTIVE` 与 `PAUSED` 都仍占前台（PAUSE 是操作者按着她，resume 才是她唯一
   合法动作），状态读不到时保持旧的指针规则。
7. **拿放 Activity 的完成是"证据绑不上"，不是"没有完成契约"。** 实测：进度证据提交了 owner 自己的
   placement fact，LR-5 仍回 `progress_state=UNKNOWN` / `completion_status=NOT_COMPLETE` —— 它按
   Activity 自己的 `target_refs` 绑定证据，而那条 Activity 的 target refs 只有机会本身的
   `world_fact:dailyact:…`。修法（`_daily_action_adoption_spec`）：把 B
   `daily_action_completion()` 的 `expected_value` 也放进这条 Activity 的 target refs。完成含义、
   `WORLD_FACT_MATCH` 判据、evaluator 都没动；修完 `progress_state=COMPLETION_ELIGIBLE`、Activity
   由 LR-5 判 `COMPLETED`，**不再用 ABANDON 代替一次性拿放已完成**。

8. **步骤就绪认识 `dailyact:`。** `life_step_readiness` 增加 `TARGET_DAILY_ACTION`，
   `canonical_target_ref` / `namespace_of` 把它与 place/object/bodyact 一视同仁；revision 读
   World 的 `world_revision`。否则一条 `dailyact:` 的 ACTIVE Activity 会被判成
   `TARGET_NAMESPACE_UNKNOWN`，把"能拿"说成"没步骤"。

9. **正式工厂保留实例世界绑定。** `resolve_world_provider` 把当前 `env` 和
   `_first(env, WORLD_HOME_ENV)` 传给真实 `WorldMoveOpportunityProvider`；目录按
   `CHIYO_WORLD_HOME` 优先、`HERMES_HOME` 回退。不依赖操作者进程默认 home，不初始化原世界。

## Alternatives considered

* **让 cycle 自己解析/拼 `dailyact:`。** 最强立论：少一个跨模块依赖，改起来快。否决原因：
  编码归 B，双份实现必然漂移；A 只调 B 的 `build_daily_action_step` / `daily_action_completion` /
  `daily_action_observed_fact`，B 的拒绝（`DailyActionTargetError`）就是具名拒绝，不猜。
* **契约没发布前就什么都不显示（等 B）。** 最强立论：边界最干净，绝不可能与 B 说得不一样。
  否决原因：主线 A 的验收要的是"模型真的看见现场"，而这份现场本来就是同一次观测的只读投影；
  因此做成**标注来源**的回退（`source: provider.observation_fallback`），B 一发布就整条走契约，
  两套结果不会混淆。
* **prompt 超限时按重要性给每块分配字节配额，再逐个字段截断。** 最强立论：信息利用率最高。
  否决原因：字段级截断正是旧 bug 的来源（切字符串=伪 JSON）。整块丢弃能把"仍是一份完整对象"
  做成结构性保证，代价只是少看几段可选的上下文。
* **裁剪时先丢候选描述以外的东西，最后才动现场。** 这是实际采用的顺序；反过来的"先压现场保研究"
  被否决：她这一天先要知道自己在哪，研究材料是次要材料。
* **把"成功即完成"用在拿放上（拿到就算这件事完了）。** 最强立论：省一次 LR-5 往返。否决原因：
  这正是设计 19 禁止的"Action 成功即 Activity 完成"；文档要求步骤与完成分开。
* **在没有 fresh 观测时用 target 的期望值当事实。** 最强立论：不会卡在 UNSETTLED。否决原因：
  target 说的是"要什么"，不是"owner 报了什么"；用它会凭空造完成。
* **A 自己写一份 dailyact 的 AR-0 动词准入。** 否决：AR-0 的动词白名单只有一个地方，重复一份
  必然漂移；因此只在那一处把 `PICK_UP`/`PLACE` 加进窄准入（仍由 World 自己的 allowlist、
  gateway 曝光和 Resolver 决定能不能提交）。

## Consequences

* 决定模型现在读到的是**带名字的现场**：她在 `家`（room）、手上有 `饭碗`、`街角小店` 走 240 秒
  且 OPEN、`小公园` 走 600 秒；"这轮真能做的"是个具名列表（含 `PICK_UP`/`PLACE`/`STAY`/`POSE`），
  不是 id 表格。研究/实践仍然在，但按需、且是 prompt 超限时**第一个**被丢的。
* prompt 永远是一份可 `json.loads` 的完整对象；超限时只丢整块并报告计数。已知代价：
  ① 极端小的 `max_prompt_chars` 下最后会整条丢候选（`MIN_CANDIDATES_IN_PROMPT=1`），
  报告里 `candidates_tail` 的计数写明丢了几条；
  ② 裁剪顺序是常量，不可配置；
  ③ `over_budget=True` 只记 warning，不阻断决策（宁可发一份大 prompt，也不发一份坏 JSON）。
* 拿放：`PICK_UP`/`PLACE` 的 step / 完成事实 / 观测判据全部来自 B 的模块；A 侧只做
  "选中→路由→重观测→核对→提交进度"。缺模块是
  `DAILY_ACTION_TARGETS_MODULE_UNAVAILABLE`，目标是畸形是 B 的具名拒绝，读不到世界是
  `STEP_UNSETTLED`，三种都不是"她做了"。
* **已知代价 / 残留：**
  1. `life_live_context_view` 的回退分支会在契约缺席时读 provider 的
     `last_observation`/`last_opportunity_view`/`last_catalog`；契约一出现就完全不读它们。
  2. `_daily_action_observed_state` 在没有 B 的 `daily_action_observed_fact` 时只认 snapshot 的
     `hands`；`PLACE` 的"放到哪张桌面"由 B 的完成事实表达，A 只核对"确实不在手上"。
  3. **一行 AR-0 准入**：`action_reality_ledger.WorldActionAdapter.validate_prepare` 的动词白名单
     加入 `PICK_UP`/`PLACE`。这不在 A 声明的写集里，但没有它 B 的拿放永远到不了 Resolver；
     改动只是窄准入（单 target、无自由 params），owner 侧的 allowlist/曝光/Resolver 一个字没动。
  4. 现场里"这轮真能做什么"的*准备条件*（`preparation`）在回退分支里恒为未知
     （`PREPARATION_NOT_KNOWN`），只有契约分支能给出。
  5. 真实旅程/停留的推进仍由 C 的 resident 按时推进；A 这边每 poll 不做新 Decision 的行为未变。

## Verification

* 新增 `tests/test_lived_day_core_v04.py`（26 个 exact 用例，**全 PASS，`EXIT=0`**，
  `PYTHONDONTWRITEBYTECODE=1`，WSL Ubuntu python3.12）：现场契约/回退（具名/未知/不越界）、
  现场进真实 prompt、prompt 超限结构化裁剪后 `json.loads` 成功且候选核心与现场保留、
  裁剪报告只有计数、cycle 把现场放进 `decision_situation_context["world"]`、
  `dailyact:` 的 step/adoption 形状来自 B 的真实契约、拿放完成只由 fresh 观测决定
  （成功/不一致/读不到三种）、畸形目标具名拒绝、模块缺失具名拒绝。
* 直接受影响的既有链：`tests/test_day_life_body_actions.py` 14/14 `OK`（真实 World service +
  gateway + Resolver，说明 AR-0 那一行准入没有退化四动词）。
* 另跑了 `tests/test_day_life_continuity.py`（21/22，1 error 在**别人**正在改的测试文件里：
  `_world_cycle() got an unexpected keyword argument 'snapshot'`，属该测试自身 helper 签名不一致，
  与本改动无关）、`tests/test_life_v1_appraisal_context.py`（1 error 1 fail）、
  `tests/test_subject_reading_cycle.py`（1 fail）。后两者失败形状相同：
  closeout 步骤走到 `owner_status=OK / effect_state=NO_CHANGE`，round 报 `ALREADY_DONE`
  而测试期望 `ACTED` —— 这条路径（`AR0_CLOSEOUT_WORLD_ROUTED` + 确定性 writer）本次**没有**
  被改动，但我**没有**为它们建立负控（没有在等价树上回退本次改动复跑），因此
  **不作"既有失败"归因**，只如实登记失败形状与位置。
* 没有 `py_compile`（用 `ast.parse` 校验语法）、没有全库回归、没有真实模型/密钥/实例、
  没有删除任何夹具（`/tmp/nyairov04a-livedday-*`、`/tmp/nyairov04*` 全部保留）、没有 commit。

* **真实链（本阶段验收中心）**：`tests/test_lived_day_chain_v04.py` 在**一个实例**上、用这个实例
  **自己装配的** `RealJourneyWorldActionAdapter(JourneyWorldResolver)`（没有测试 router、没有替换
  resolver）跑完：读到真停点（`READING_EXHAUSTED`）→ 明确 `WAIT` park（owner=`WAITING`）→ 真实
  `PICK_UP`（基底 `hand/left_hand`，`re_read=True`，`progress_state=COMPLETION_ELIGIBLE`，
  Activity `COMPLETED`）→ 真实 `PLACE`（基底 `kind=room / home / bedroom`，Activity `COMPLETED`）
  → 真实外出旅程到达 `street_front` → 回家 → 真实停留 300s 的 `END` → owner 发布材料下一版 →
  LR-4 upkeep（`changed=1`、`auto_resumed=False`）→ 显式 `RESUME` **同一条** Activity →
  `CONTINUE` 读新版第 1 页。`Ran 1 test in 34.1s ... OK`，**exit 0**（WSL，
  `PYTHONDONTWRITEBYTECODE=1`）。夹具复用 `test_world_daily_life_continuous.EpisodeFixture` /
  `test_life_v1_integration.DScenarioBase` 的真实 World service 与真实 runtime，唯一替身是回放原始
  JSON 的决策脚本；时钟是注入的（明示非真实一天）。
* 同轮相邻（只跑受影响的）：`tests/test_lived_day_core_v04.py` 26/26 `OK`（exit 0）、
  `tests/test_day_life_body_actions.py` 14/14 `OK`（exit 0，其中"委派动词恰好是 CONSUME/POSE/REST"
  的旧断言按 B 的常量更新为含拿放）、`tests/test_world_daily_life_consume.py` 6/6 `OK`（exit 0）。
* 未归因的现状（无负控）：`tests/test_day_life_continuity.py` 21 pass / 1 error（该测试文件自身
  helper 签名 `_world_cycle(snapshot=…)` 不匹配，本包未改它）；`tests/test_world_daily_life_continuous.py`
  1 fail（continuity binding 的 `continue_from_ref` != `owner_result`）。
* 父级最终接线回归：`tests/test_world_provider_instance_binding.py` 两项通过，Python exit 0、
  WSL exit 0、AST PASS；正式 factory/provider 为真，transport readiness 与 catalog reader 是替身。
  覆盖非默认实例目录、world home 优先和 profile fallback；不是原实例 live 地点显示成功的证明。
* 原实例有限 Gemini 运行新增 1 请求（账本 3→4），结果 `DECISION_FAILED / MODEL_PROVIDER_ERROR`，
  没有合法 Decision/动作。正式装配未提供 cognition observer，未取得更细 HTTP 错误元数据；
  不归因具体状态码，也不把后续 NOT_DUE 当她主动休息。完整边界见父级阶段结果。
