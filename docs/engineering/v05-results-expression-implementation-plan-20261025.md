# 0.5第一块可施工方案：具体评价进入后来判断，表达保留有限性

状态：最小方案已获本次最小实施批准，并已按批准范围完成离线 A/B 验收（见文末「实施事实」）。
基线 1057bad9d449f0458358974f2a4e5f94219ad6a3。本文不授权真实模型、外发或旧实例操作。

## 三处核查的结论

### 1. 结果不是完全没回流，缺的是具体评价的有限呈现

已存在的持久事实有：Decision中的method_ref/method_trial、Practice owner保存的trial明细与方法状态、作品版本及step receipt、独立Memory经历准入。下一轮和重新装配后都从owner重读，TRIAL/CONTESTED状态已能进入判断，不需要再造使用记录库才能产生影响。

当轮解析出的method_usage含offered head和本轮receipt refs，主要是回合返回值及进程内状态；本次所查正式路径未找到它独立持久化。无显式trial时，不能承诺跨重启精确恢复“当时使用了哪个方法版本”的完整关联。

更小而明确的读侧损耗是：现有方法列表已含trial_count/counterexample_count，但最终adapter未保留；owner已保存trial明细，正式决策读路径未消费这些具体评价。它们是可复用的现有事实，不等于已证明真模型自然采用。

### 2. j的失败不作为这轮Memory修复单

父级独立读取仓库安全导出，确认存在memory域DELIVERY_FAILED/err:memory:ProductionExecutionRefused，同时另有lexp经历投递DELIVERED/ACCEPT和VERIFIED_EXECUTION_OUTCOME证据。

worker静态定位：当前生产装配注册拒写的settlement Memory consumer，能产生相同错误形状；经历准入走另一个consumer。AR1各消费者失败隔离，不自动回滚已结算结果。由此不能说Memory全断，也不能将此失败直接认定为方法评价的全局前置阻断。

历史j的具体投递event关联、内部错误码和构建同一性不足，故仍不能唯一还原该条历史因果。不能把“现树匹配一种拒写机制”写成“j现场根因已证”，更不能为了清红移除拒写消费者或补写历史。

本轮不改Memory admission、settlement consumers、outbox重试和旧j。若以后要改这条消费者路线，必须单独明确准入职责与持久合同，不能顺手处理。

### 3. 表达view已有很多事实，但两处信息在接缝丢失

最终请求已有活动/能力摘要、world/body未知、contact与shared-practice局部事实，以及草稿正文摘录、来源版本/新鲜度、延后、扣留原因、固定local:owner/local_inbox边界。不是“整个prompt没有标题”，也不是只有sharing.sources。

本次静态追踪的窄缺口：sharing.sources只保留id/kind，来源身份与其它块的标题没有可靠显式配对；sharing block生成的truncated在adapter被丢弃，模型不知道展示列表不完整。

上一轮拒发原因不进入下一轮view是另一条跨回合信息问题，不自动升级成本轮修复目标；下一轮仍可读当前草稿状态与source_current，不能由缺last_sharing投影推成没有拒绝承接。来源可读性也不等于对某人可披露权，不新增披露授权字段。

## 推荐选择

选“复用现有owner事实的有限读视图”，不选“新增持久使用记录”。同一个worker圈齐方法与表达的相邻接缝，一次完成正式链和验收。

方法侧仅展示现有、可治理读取的显式评价，不承诺所有未评价使用都被记住；表达侧仅复用同轮已取得的信息，不额外读作品来凑标题。两边不新建owner、cache、ledger或schema，不自动评价、不改人格。

代价是方法详情最多新增3次owner读取/决策回合；若次数/延迟不可接受，可先仅透传已读计数，但那只能交付“次数与状态可见”，不能冒充具体评价已回流。新增持久usage能补无trial精确归属，但代价与合同更大，本轮不选。

## 功能目标与完整写集（拟定）

目标：后续回合及重新装配后，模型能读到既有方法的评价次数、反例数与有限的最近评价事实；可选本地表达时，来源名称能与id配对，列表截断不会伪装完整。

拟业务写集：
- components/life/scripts/life_practice_context.py：通过既有PracticeSeam/read_method做有界详情投影，若无需此处修改就不动；不复制owner判据。
- components/life/scripts/autonomous_life_cycle.py：仅方法上下文富化、sharing来源同轮元信息接缝；不改Decision/Activity/Action状态机。
- components/life/scripts/agency_cognition_adapter.py：practice与sharing有限白名单及必要裁剪，既有结构下加有限事实，不改外部动作词表。
- 新tests/test_v05_results_expression_views.py与scripts/v05_results_expression_smoke.py；必要tests/test_result_method_behavior.py、tests/test_v04_sharing_life_chain.py只补断言不放宽原断言。
- 原个人方法/有限表达note就地同步，新决定若确需独立记录，按既有notes规范归位；本方案文档更新实际验收和代价。

不改personal_practice_service的owner合同、AG1评价证据门、Memory/AR1、Sharing owner的受众/通道、安全开关、World/Body、预算、旧root、发行部署。

## 实现必须保持的细节

1. 先按本轮实际offered方法集合和现有最多3条上限定读集，不为工作台全库逐条read。只读计数为真整数，缺值保持未知，不把缺失改成0。
2. read_method使用既有治理读口，只投影被允许读取的最近一条显式trial的outcome/受评方法版本和必要的有界证据计数；不带detail正文、私人经历片段或原始prompt。若治理扣住相关信息，不从文档原文、缓存、Decision或旧版本旁路恢复。readable不冒当前evidence_usable；历史评价不冒本轮重新确证。
3. 列表head与详情head不一致时，不把不同版本内容拼成同一评价快照。具体使用既有版本字段判定，无法核实就给未知/未富化，不新增CAS写入，不再追读到一致为止。
4. 方法详情读取失败保留已有可读列表状态，另具名详情未知，不把失败当“没有评价”；不让该错误自动使方法退休或改变合法手柄。退休/撤源仍沿原合同，写成功仍不自动HELPED。
5. 重启后从同一owner重建上下文，不靠last_method_usage。无trial时明确没有可提供的显式评价，不推断没用过、不伪造精确offered head。
6. sharing来源title仅从本轮已取得且确认同一artifact身份的数据取；reading/作品缺标题或身份对不上就省略，不能凭相似标题配对。若版本字段已真实可得且合同允许可保留，否则本轮不强加；标题不替代版本或授权。
7. truncated只表示该sharing列表被源侧有界裁剪；模型预算后若整个sharing/practice块被移除，仍按既有裁剪规则，不以此标记冒完整payload保留。不得发明关系立场/披露许可。
8. 不把额外owner读当无成本，不加额外模型回合，不自动prepare/评价/发送，不为了展示触发新行为。

## 固定最小验收（建议写入正式工单后执行，本次未跑）

统一WSL仓库cwd，PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B。

A：-m unittest -q tests.test_v05_results_expression_views tests.test_result_method_behavior tests.test_loss_continuation_behavior tests.test_v04_sharing_life_chain

B：scripts/v05_results_expression_smoke.py

A/B应证明：
- 用真实既有owner保存一条有证NOT_HELPED评价，下一轮正式请求含CONTESTED、计数和对应版本的有限评价；重新装配读同owner仍相同，不手塞最终context证明自身。
- 评价证据不足/不可读、退休、详情故障、列表与详情换head分别验证；不泄露扣留正文、不自动HELPED/退休、不把不同版本拼接。最多3次详情读，0个新事实存储写口。
- 无trial时不虚构使用版本/结果；无step回合不造receipt；原方法证据、CAS与UNKNOWN约束保留。读视图重建不写owner；测试建立评价时的必要写入与纯读阶段分开计数。
- 正式assemble→evaluate_once→AG1原始JSON脚本Decision捕获实际决策请求，验证reading来源id/title配对、无标题作品不编、truncated透传。实际分享仍走原prepare/defer/send_local和来源扣留拒发，原断言保留。
- 使用脚本模型替身，真实Supply/Practice/必要Memory owner，隔离临时root且finally清自有服务/lease；不产品模型、不外发、不触旧实例。重装配只证明既有owner重读，不擅称完整resident crash恢复。

父级独立只复跑工单A/B，不全量套件/notes全树/SHA/浏览器仪式；报告脚本Decision、真owner、自然模型三层，不把A/B绿称真模型自然采用。上线或产品观察另批。

## 停止门

- 要求“没有trial也跨重启恢复精确使用版本/后果”时停，需持久契约选择。
- 现有治理读口无法安全给出trial元事实、需要新增权限/owner字段/绕扣留时停；不为显示详情扩证据权。
- 要持久化上一轮拒发原因、改变撤源方法可用性、移除RefusingSettlementConsumer、改Memory准入时停，不算只读view修复。
- 要改关系/披露规则、受众通道、默认gate/预算、外发/微信或旧实例时停。
- 同一测试两轮红且原因未知时停，保留产物，不能用放宽原断言求绿。

## 本次核查与证据边界

结果/Memory worker exit0；JSON实际命名-result-evidence.json而非任务约定-evidence.json，已据实定位。表达worker达到30轮上限exit1，但报告与JSON已落盘，父级读取并解析有效；这不是完整成功交付，也不是无成果，本轮不自动整单重试。其“新增owner读必然违反单读原则”“拒因进下轮必然要持久格式”等绝对论断不采：它们是成本/恢复语义选择，不是普遍禁令。

worker对method_usage“全仓无持久化”和“关系立场无owner”等话，仅按已扫正式路径理解；不作为全库数学穷尽。j只确认导出中的两种结果并存与静态拒写机制匹配，不承诺历史根因闭合。

父级本次仅做JSON解析、具名安全证据核对、文档方案检查；未跑行为验证，工作树业务源码无新增改动。产品0新增，历史85/300余215沿用。

证据：[结果与Memory核查](../../.agents/worker-runs/20261025-v05-result-memory-seams-result.md)、[表达核查（达到轮限后留存）](../../.agents/worker-runs/20261025-v05-expression-view-seam-result.md)、[j安全导出](../../.agents/worker-runs/20261024-fastday-j-final-audit-result.json)、[0.5五连接范围](v05-chapter18-scope-20261025.md)。本文件为父级限定方案；原worker报告过强表述以本文边界为准。

## 实施事实（2026-10-25，本窗口）

最终业务写集为原方案的三个接缝与同功能返修的两个截断读侧：`life_practice_context.py`（`evaluations_for` 有界投影、列表
`trial_count` 进入 decision view）、`autonomous_life_cycle.py`（每回合一次富化、同轮记录
已核实的作品标题、来源标题配对）、`agency_cognition_adapter.py`（计数/评价/来源标题/
`truncated` 白名单与一句 SYSTEM_PROMPT 说明）、新增
`tests/test_v05_results_expression_views.py` 与 `scripts/v05_results_expression_smoke.py`；
两篇原 note 原地同步，另新增
`.agents/notes/implemented/feature/2026-10-25-v05-results-expression-views.md`。
`tests/test_result_method_behavior.py`、`tests/test_v04_sharing_life_chain.py` 只补断言。

实际成本：每个正式决策回合最多 3 次 `read_method` 详情读（同回合重复 context 调用不再加读）；
分享来源标题复用本轮已读数据，0 次新增 owner 读。

固定验收（WSL，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`）：

- A `-m unittest -q tests.test_v05_results_expression_views tests.test_result_method_behavior tests.test_loss_continuation_behavior tests.test_v04_sharing_life_chain`
  → 父级最终独立复跑 `Ran 43 tests in 29.968s ... OK`，exit 0，日志 `.agents/worker-runs/20261025-v05-results-expression-final-parent-a.log`。
- B `scripts/v05_results_expression_smoke.py` → 截断返修后 `checks: 37, failed: 0`，
  `RESULT: PASS`，父级最终独立复跑 exit 0，日志 `.agents/worker-runs/20261025-v05-results-expression-final-parent-b.log`。

边界：Decision / 内容仍是脚本替身，真实 owner 覆盖 Supply / M0 / Practice / Sharing；
不宣称真实模型自然采用、G1、24h 或完整 resident crash 恢复。没有 usage 存储、Memory 门、
owner 合同、默认 gate、预算、旧 root、外发或部署改动。

### 收尾（2026-10-25，原工单两项正式覆盖）

- 真实 Supply 阅读材料：实例 prepare 时由 owner 创建并写入既有绑定，正式
  `assemble → evaluate_once → AG-1` 请求的 `sharing.sources` 里 `reading_material`
  与同轮 `refresh=True` 读到的 `material.title` 成对出现，adapter 白名单同样保留；
  生产路径没有为标题新增 owner 读（B 中 3 项 reading check）。
- 两层 `truncated` 读侧漏接已按父级明确裁定修复（只改既有字段的准确投影）：
  - `life_sharing_service.list_shares` 现在合并 owner `list_projects.truncated` 与本层
    `cap` 裁剪；101 条真实草稿 → 返回 100 行、`truncated=True`，`counts.total` 仍是
    当前答案的 100 行（不冒充全量）。未翻页、未改 `MAX_LIST` / 过滤 / 排序 / 写口。
  - `life_sharing_context.prompt_block` 在 6 条 OPEN 草稿（> `MAX_PROMPT_SHARES=5`）时
    报告 `truncated=True`，展示 5 条；第 6 条仍是 DRAFT、无 receipt。
  - 缺 `truncated` 字段的 owner 答案保持 `False`，不发明截断。
  真实 owner 回归在 `RealOwnerShareTruncationTest`（A 内，6/101 条草稿），正式链在 B 的
  独立根截断链（7 项 check：AG-1 实际请求 `truncated=true`、展示 ≤5、6 条全部存活）。

### 剩余未验

- 真实产品模型是否会用上这些字段（计数/评价/来源标题/truncated）未验。
- 3 次详情读对真实回合延迟的影响未测；只在离线计数上确认上限。
- 截断样本证明的是读侧投影与"未展示草稿不被自动 drop/发送"，不是真实模型看见后会如何选择。
