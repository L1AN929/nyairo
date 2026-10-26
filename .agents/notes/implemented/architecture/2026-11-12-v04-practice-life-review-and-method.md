# Agent Note: v0.4 C —— 确证经历 → 自主回顾 → 有范围做法 → 真实使用与有证据评价

Status: implemented

## Problem

B 的实践工作台（`PersonalPracticeService`：review / method / trial）与 A 的记忆服务（`LifeMemoryService`：
`status/list_experiences/read_experience/context`）都已经存在，但**主链一处都没接**：

1. 她的经历只能通过 `experience_provider`（port 的 `prompt_view`）进决策/写作提示，没有"相关检索、可引用 id"
   的入口；所以她根本无从"回顾某件确实发生过的事"。
2. 候选集里没有"回顾"这种选项：`life_project_options` 只会从疑问、到期项、资料命中长选项。
3. 决策模型即使想"照着自己写过的做法做"，也没有任何合法字段可写：DecisionRecord 里没有 `method_ref`，
   AG-1 也不认识它；写进 prompt 的 `situation_context` 里更没有她的做法。
4. 试用效果（HELPED/NOT_HELPED）没有真实入口：没有任何路径把"我用了这条做法 + 这次的结果 refs"关联起来，
   于是唯一能反映"效果"的信号只剩"版本写成功了"——那正是设计 §4.5 明确禁止的自动晋升。
5. AG-2 交给 Memory 的 handoff 只有 refs，没有"这次到底改了什么"的可读描述，经历行只能是"某个工件变了"。

## Decision

一句话：**C 只做接缝与主链编排——把 A/B 的既有 owner API 包成有界只读（+ 三个 owner 写）的 `PracticeSeam`，
让它长出"回顾"选项、让决策能命名一条做法、让写作提示读到做法的做法/范围/未知、让真实结果带上后果 refs，
并让评价只经 B 的证据校验；marker、记忆准入、owner 判定全部仍归 A/B。**

1. **新接缝 `life_practice_context.PracticeSeam`**（C 独占，`components/life/scripts/life_practice_context.py`）：
   `experiences/read_experience/methods/read_method/reviews/read_review/writing_context/decision_context/
   create_review/create_method/record_trial/fingerprint_parts/facts_of`。envelope 以 `ok` 为准（B 的写成功是
   `status="applied"`）；marker 只借 B 的 `parse_practice_content`；服务缺失 → `NOT_WIRED` + `last_error`，
   绝不当"她没有实践"。检索至多两次有界查询（任务 query → 空 query），仍是 B 自己的检索，不建第二个索引。
2. **回顾选项**（`life_project_options.review_seed` + `KIND_REVIEW`）：只有工作台真的接线、且 A 真的返回可读经历时，
   才出现**一个**回顾选项；`experiences` 就是那些行（id + 时间 + 片段），身份含经历 id（换经历=换选项）。
   没有经历就没有选项——"写一段读不回来的过去"不是选择。
3. **选择即创建，复用既有法定路径**（`cycle._review_adoption_spec`）：候选仍是 `PERSONAL_OPPORTUNITY` 的
   `popt:` ref，选中后 `create_review` 真建作品，随后 AR-0 写第 2 版；`operation_key` 由"选项 + 经历集"派生，
   UNKNOWN 解析后重试不会多建一件。回顾是普通可续写作品。
4. **做法提案合法化**（AG-1 `validate_method_proposal` + `DecisionRecord.method_proposal`）：一条有范围的做法
   草案（title/instruction/scope/uncertainty + 可选 experience_ids/review_refs），提交后由 cycle 经
   `create_method` 保存为 TENTATIVE，`operation_key` 由 decision_id 派生。它**不是**已验证 Skill、不是偏好、
   不是信念、不是可执行程序。
5. **`method_ref` 的校验/记录/使用全接通**：AG-1 `validate_decision_output(..., allowed_method_refs=…)` 只接受
   **本轮真的给出去**的手柄（`method:<sha16>`，由 artifact id 派生、不含 owner id）；`DecisionRecord.method_ref`
   落盘并往返；cycle 在 `_bind_method_usage` 用本轮读到的 head 解析它，在 `_finish`（`_settle_method_binding`）
   把 owner 真实回执的 `consequence_refs` / `version_written` / `step_effect` 挂到同一个 `method_usage` 上。
6. **有证据的评价**：`method_trial`（Decision 可选键：method_ref + outcome + detail + experience_ids）经 AG-1
   校验后，由 cycle 调 B 的 `record_trial`（CAS 到本轮读到的 head）；seam 在写之前先用 A 的读复验证据，
   `HELPED/NOT_HELPED` 无证据直接拒（只剩 `UNKNOWN`）。**版本写成功永远不会被当作 HELPED**。同一窄动作也可
   作为显式的可选评价（不做每轮额外模型调用）。
7. **提示真的看到做法**：`agency_cognition_adapter._bounded_situation` 增 `practice` 块（方法 3 条 + 经历 4 条，
   全字段截断），`SYSTEM_PROMPT` 增 `method_ref/method_trial/method_proposal` 规则与"situation_context.practice"
   说明；`model_continuation_writer._user_prompt` 把 `practice_context` 与 `research_context` **分两个键**送进
   prompt（旧的裸 mapping 形态语义不变），并明确"做法只是参考、不能声称被执行"。
8. **做法不是作品**：`cycle._non_work_artifact_ids()` 让方法文档退出续写/收尾候选（与研究资料同一机制），
   回顾仍是普通作品；`life_step_readiness.SITUATION_KEYS` 增 `practice_experiences/practice_methods/practice_reviews`
   （只有 id/头版本/状态词，没有文本、没有时间），一个 tick 不会变成新的回顾配额。
9. **可读的确证结果进 Memory**：AG-2 handoff 增 optional `owner_confirmed_context`
   `{artifact_id, version_ref, title, result_description, outcome_confirmed:true}`，由 cycle 在**真的读回该版本之后**
   从 owner 的正文生成（tail 240 字，剥掉通用 Markdown 头），边界处 `validate_owner_confirmed_context` 只做结构
   校验；A 的 consumer 再按它自己的规则核对引用。writer 的 `self_note` 从不进入这里。
10. **生产接线在装配**（`autonomous_life_assembly.resolve_practice_service`）：用 B 的 `practice_from_env(port, env)`
    在同一套 env/bindings 上建服务，`assemble_autonomous_life(practice_workspace=…)`；报告 `assembled["practice"]`。
    resident 未改、未启用原实例、未改 env。
11. **2026-10-24 结果→做法→评价接缝修正（本树）**：`_bind_method_usage` 只在**本轮提供给
    Decision 的那次读取**（`_round_methods`）里解析 `method_ref`，不再用会被写作提示的方法检索
    覆盖、且可能已换 head 的 `PracticeSeam.last_methods`；`_settle_method_binding` 的
    `consequence_refs` 只来自 owner 的 step receipt，无 step 的回合（NO_ACTION/WAIT/PAUSE）
    保持空表，不再把做法文档自身当成结果。评价仍只经 B 的证据门；成功写版本仍不会自动
    `HELPED`；没有新增状态、字段语义或评价类型。

## Alternatives considered

* **让 C 自己按 B 的 marker 拼一份回顾/做法文档，只调 `create_project`。** 最强立论：不用等 B 的 envelope，立刻能跑。
  否决原因：那就是**第二份 marker 定义**，B 一改字段两边立刻漂移；资料的"证据核对/治理撤回"也会变成 C 自己的
  一套。阶段单明确 marker 由 B 唯一维护，C 只调 API。
* **把回顾/做法写进 Memory，当成 experience 让"经验回用"更彻底。** 最强立论：她"写下过的东西"确实是一种经历，
  而且 Memory 已有准入治理。否决原因：回顾是**派生文档**，做法是**暂定草案**；直接写进 Memory 就等于让模型文字
  自我晋升为已准入经历，还会让"删除经历后不得再透出派生正文"这条治理失效（A/B 的 `read_review` 正因此会在证据
  不可读时扣住正文）。
* **给 AG-0 加一个 `REVIEW_PROJECT` source_kind。** 最强立论：候选一眼能分类。否决原因：`candidate_sources_ag0`
  的禁列就是为了防止发明类别；回顾选项本来就是她的**个人机会**，`PERSONAL_OPPORTUNITY` + `popt:` ref 零新增类别，
  且自动复用 AG-1 legality 与 AG-2 adoption。
* **给 `method_ref` 单独一个 owner 句柄表/第二存储。** 最强立论：手柄可长期复用，不受"本轮读过"限制。否决原因：
  那就是第二个索引/第二个库；`method:<sha16>` 只从 owner id 派生，本轮读到的集合就是唯一合法允许列表，
  "模型不能发明标识符"这条继续成立。
* **让 Decision 直接带 `HELPED` 而不校验证据，交给 B 拒。** 最强立论：少一次 A 的读。否决原因：那会让"无证据的
  成功"变成一个可以在 AG-1 里合法落盘的 Decision，再由下游拒绝；seam 侧先复验能让失败发生在写之前，
  并且 UNKNOWN 才是无证据时唯一诚实的词。
* **把方法的正文也交给 AR-0 自动续写。** 最强立论：现有 AR-0 路径一条都不用改。否决原因：做法是参考文档，
  自动续写会让"她的做法"被模型静默改写；改写必须走 B 的 `revise_method`/`update_document`（保留旧版本与反例）。

## Consequences

* 现在可以：一次真实确证的作品修改 → 经历进入 Memory（行文是"她写了什么"的有限描述，不是"某工件变了"）→ 下一轮
  决策处境出现**一个**回顾选项和一个可命名的方法手柄 → 选中回顾 → B 真建作品 + AR-0 真写具体回顾 → 同一轮
  Decision 可以提出一条有范围做法并被保存 → 再下一轮的真实写作提示带着 instruction/scope/uncertainty →
  真实结果与"用了哪条做法、哪个版本、哪些后果 refs"绑定 → 有证据的 HELPED 让它到 TRIAL，反例让它到 CONTESTED。
* 安静仍然安静：没有经历就没有回顾选项；同一处境指纹不变就不重问；`NO_ACTION`/`no_change` 依旧合法。
* 已知代价与残留：
  1. B 的 `create_review` 不返回 content，C 要"可读描述"就自己再读一次 owner（多一次只读）。
  2. B 的 `record_trial.artifact_id` 是 keyword-only（阶段单写的位置参数），C 全按关键字调。
  3. B 的检索是子串匹配，任务标题常常命中不到；seam 做一次空查询兜底，因此"最近的做法"总能看到。
  4. 该 fixture 里第二个 Activity 结束后 CURRENT_ACTIVITY 候选不再 CONTINUE 合法，所以链测试的"反例评价"走
     阶段单允许的**显式窄评价动作**（`PracticeSeam.record_trial`，真实 owner 写 + 真实证据校验），不是第三轮 Decision。
  5. 没有 UI/手机面（B 的所有权）；没有真模型/密钥；fixtures 留在 `/tmp/nyairo-v04-practice-fixtures` 不清理。
  6. **2026-10-24 提案链与确证描述断点已修（本树）**：
     * AG-1 的 `validate_decision_output` 早已接受并校验 `method_proposal`，但 `DecisionRecord` 构造
       漏传该字段，`record.method_proposal` 恒为 `None`，`_save_method_proposal` 永远没有输入。
       现只在构造处补传同一份**已校验**结果（`validated_output.get("method_proposal")`），没有放宽
       AG-1 合法集、字段、截断或预算，也没有把原始模型字典绕进 record。
     * 回顾链的 `confirmed_used=False` 不是 Memory 或 handoff 的问题：AR-0 适配器持有自己的
       `LifeArtifactPort`，写操作不经过 cycle 的 `RoundCachedArtifactPort`，所以本轮 memo 里仍是
       **写前**的 head；`_owner_confirmed_context` 用它和本次新版本比对失败，返回 `None`。
       现在该函数在刻意回读"owner 刚确认的这一版"之前先 `_invalidate_owner_reads()`，与模块既有
       "显式复查先丢 memo"的约定一致；没有改缓存键、轮次边界、owner 契约或写入路径。
  7. 以上是同一功能内的接线与缓存一致性修正，不是新增评价类型或自主规则；真模型的自然提案/采用、
     个人方法"自然形成"、G1 与 24h 均未在此证明。

## Verification

* `tests/test_v04_practice_life_stage.py`：20 条（13 纯逻辑 + 4 AG-1 契约 + 3 真实 owner 链）。
  * WSL（Python 3.12）：`python -m unittest -q tests.test_v04_practice_life_stage` →
    `Ran 20 tests in 12.669s … OK`，退出码 **0**（本轮同一命令复跑）。
  * 新增 2 条具名控制：未提案时 `DecisionRecord.method_proposal` 默认 `None` 且往返仍 `None`；
    同一 Decision 的保存意图（同一 owner operation key）重放不会产生第二份草案。
  * 方法链的提案现在来自正式 Decision 原始 JSON，经 AG-1 校验、DecisionRecord 提交、cycle
    `_save_method_proposal` 保存；不再由测试预建草案或手调保存函数。
* 直接受影响的既有链 60 条（`test_v04_research_life_stage`、`test_model_continuation_no_change`、
  `test_day_life_quiet_runner`、`test_day_life_decision_legality`、`test_v04_autonomous_day_stage`）→
  `Ran 60 tests in 11.347s … OK`，退出码 **0**。
* 真实链证明的是：真 Supply（自带 Unix socket）+ 真 M0（EvidenceStore + `MemoryAdmissionExperienceConsumer`）+
  AG-1 原始 JSON 校验 + AG-2 adoption + AR-0；决策回答与内容回答是**脚本替身**（文件里明示），不宣称真实模型自主。
* 没有跑全库回归、没有跨进程 qualification、没有动原实例/配置/密钥、没有提交或部署。
* **2026-10-25 有界评价投影（v0.5 第一块）**：列表行原有的 `trial_count` / `counterexample_count`
  现在真的进入最终 decision practice 白名单；`PracticeSeam.evaluations_for` 只对本轮实际
  offered 的最多 3 条方法各做**一次**既有治理 `read_method`，取最近一条显式 trial 的
  `outcome` / `version_id` / `evidence_count`（永远不带 `detail` 正文）。列表 head 与详情 head
  不一致时给 `PRACTICE_METHOD_HEAD_CHANGED`，不拼接、不再追读；读失败给
  `PRACTICE_METHOD_DETAIL_UNAVAILABLE` 并保留可读列表；owner 扣留证据时给
  `PRACTICE_EVIDENCE_WITHHELD`，不从原文/缓存/Decision 旁路恢复；没有显式 trial 时给
  `PRACTICE_METHOD_NO_EXPLICIT_TRIAL`，不推断"没用过"。cycle 每回合最多富化一次，重复调用
  同一回合的 context 不加读；纯读阶段 owner 零写，重新装配从同一 owner 读回同样事实。
  它不新增 usage 存储、不改 owner 判据/CAS/HELPED 证据门。证据：
  `tests/test_v05_results_expression_views.py`、`scripts/v05_results_expression_smoke.py`。
* **2026-10-25 v0.5 第二块——评价承接到同一作品后续实践（含退休损失）**：没有新增业务规则；
  已有的正式阶段在同一条真实 owner 链上被贯通验证：同一作品 A 的真实结果成为可回忆经历 →
  正式 Decision 的 `method_proposal` 由 owner 存 TENTATIVE → 后续 `CONTINUE` A 命名该做法
  （本轮 offered head 绑定，writer 的正式请求带 instruction/scope/uncertainty）→ 真实 owner
  写 A 的下一版，`consequence_refs` 来自 step receipt → 同轮 `method_trial` `HELPED` 推进
  TRIAL、`NOT_HELPED` 推进 CONTESTED → **下一轮真实决策请求的 `situation_context.practice`**
  读到状态、计数与有界评价投影，并可继续同一 A。另有可续作品 B 且本轮绑定改到 B，每一步仍写
  A、B 版本不变（沿用 [过去到候选](../feature/2026-10-24-past-to-candidate-owner-bridge.md) 的
  owner 目标优先规则）。退休沿 [损失→承接](../feature/2026-10-24-loss-to-continuation-owner-bridge.md)：下一正式
  回合旧手柄被 AG-1 `INVENTED_METHOD_REF` 拒且无 Decision 提交，不引用旧手柄的合法 `CONTINUE`
  仍写 A，writer 请求不含退休指令，档案/版本/正文保留。证据：
  `tests/test_v05_practice_continuation.py`、`scripts/v05_practice_continuation_smoke.py`；
  Decision/内容仍是脚本替身；该次记录的第二轮 handoff 是**当时 Activity revision 粒度**下的
  `DUPLICATE`（没有造第二条经历）。2026-10-25 起 owner 确证的进度改走结果级身份，见
  [进度交接的结果级身份](2026-10-25-v05-progress-result-handoff-identity.md)；旧粒度没有删除，
  缺省调用仍沿用它。不证明自然采用/G1/24h。
* **2026-10-26 v0.5 第五条——多情境/反例/修订/退休（跨两个真实作品）**：没有新增业务规则；
  两个不同 Activity（项目 A、回顾 B）各自由真实 owner 写出版本与结果级 handoff 经历，
  A 的 `HELPED`（证据 A 的经历）→ TRIAL、B 的 `NOT_HELPED`（证据 B 的经历，不是 A 的）→
  CONTESTED；下一轮正式请求读到反例计数 1、被评价版本与有界评价（无 detail），正评价没有
  抹掉反例。owner `revise_method`（CAS）回到 TENTATIVE、旧版与两条评价保留，下一轮正式请求
  与 writer 绑定新 head/新指令；已展示旧 head 后另一 owner 写者修订，旧 head 的 trial 被
  CAS 拒绝且不重绑。退休后旧手柄被 AG-1 拒且无 Decision 提交，不引用旧手柄的合法 CONTINUE
  仍写 B、writer 不含退休指令；同一 runtime 重装配从 owner 重读历史，纯读零写。证据：
  `tests/test_v05_practice_counterexample.py`、`scripts/v05_practice_counterexample_smoke.py`；
  Decision/内容仍是脚本替身，不证明自然采用/G1/24h。详见
  [v0.5 多情境验证](../../../../docs/engineering/v05-practice-counterexample-20261026.md)。
* **2026-10-24 接缝修正验证**：`tests.test_result_method_behavior` 12 例 OK（offered head 绑定、
  无 step 不冒结果、stale head 由 owner CAS 拒绝、无证据/证据撤回、反例 CONTESTED、下一轮
  `decision_context` 可见、重复结算只写一次、成功写版本不自动 HELPED）；真实隔离
  Supply socket + M0 + 正式 AG-0/AG-1/AG-2/AR-0 冒烟 27 项 check 全绿、`RESULT: PASS`，
  含另一 owner 写者在决策读之后修订做法的 CAS 负控。冒烟的做法草案来自正式 Decision 的
  `method_proposal`（经 AG-1 校验与 cycle 保存），不再预建。决策与内容是脚本替身，不称真实模型采用。
