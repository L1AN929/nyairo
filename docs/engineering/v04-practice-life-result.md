# v0.4 C 主链产品集成 —— 经历 → 自主回顾 → 有范围做法 → 使用与评价（实际交付）

一句话：**她确证过的结果现在真的会变成可检索的经历；这些经历会真的长出一个有界的"回顾"选项；她选中后 B 的
owner 真的建出回顾作品，AR-0 真的写下去；她可以结构化地提出一条有范围的做法并被保存；下一轮的真实写作提示与
决策提示里带着这条做法的做法/范围/未知和一个可校验的 `method_ref`；后续真实结果会带上用了哪条做法、哪个版本、
哪些后果 refs，并且只有"有可读证据的评价"才能记录试用（版本写成功不会被自动当成 HELPED）。**

不是新人格总库，也不是"全 ZIP 完成"；不宣称真实 Gemini 的自主选择已被验证（见"脚本替身"）。

## 1. 实际能力（每条都有代码位置与测试）

| 能力 | 实现位置 | 证据 |
|---|---|---|
| 经历检索接缝（只读、治理后、相关检索） | `components/life/scripts/life_practice_context.py:PracticeSeam.experiences/read_experience` | `tests/test_v04_practice_life_stage.py::PracticeSeamRefusalTest`、`PracticeLifeChainTest` |
| 有界"回顾"选项（每条都可能被真读回来） | `components/life/scripts/life_project_options.py:review_seed` + `build_options(experiences=…, practice_available=…)` | `PracticeReviewOptionTest` |
| Decision 明确选中 → B `create_review` 真建作品 → AR-0 写具体回顾 | `autonomous_life_cycle.py:_review_adoption_spec/_review_operation_key/_project_adoption_spec` | `test_a_confirmed_result_becomes_an_experience_and_her_own_review` |
| 结构化做法提案 → committed Decision 之后经 B `create_method` 保存（TENTATIVE） | `agency_decision_ag1.py:validate_method_proposal`、`autonomous_life_cycle.py:_save_method_proposal/_method_operation_key` | `DecisionPracticeContractTest`、`test_a_scoped_method_is_saved_then_really_used_and_evaluated` |
| 后续 task 相关做法进入真实决策/写作 context（做法/范围/未知 + 头版本） | `life_practice_context.py:methods/writing_context/decision_context`、`agency_cognition_adapter.py:_bounded_situation`、`model_continuation_writer.py:_user_prompt` | 同上（断言 prompt 的 `practice_context.methods[0].instruction/scope/uncertainty`） |
| 模型可选 `method_ref`：校验 / DecisionRecord / 使用路径全接通 | `agency_decision_ag1.py:validate_decision_output(allowed_method_refs=…)` + `DecisionRecord.method_ref`；`autonomous_life_cycle.py:_bind_method_usage/_settle_method_binding` | `DecisionPracticeContractTest`；`method_usage.consequence_refs` 断言 |
| 有证据评价走 B `record_trial`（HELPED→TRIAL / NOT_HELPED→CONTESTED / UNKNOWN 不变） | `agency_decision_ag1.py:validate_method_trial`、`life_practice_context.py:record_trial`、`autonomous_life_cycle.py:_settle_method_binding` | 链测试（HELPED 走 Decision；反例走显式窄评价动作；无证据 HELPED 被拒） |
| 确证结果带着可读描述进 Memory（不是"某个工件变了"） | `life_integration_ag2.py:validate_owner_confirmed_context` + `emit_experience_handoff(owner_confirmed_context=…)`；`autonomous_life_cycle.py:_owner_confirmed_context/_emit_progress_experience` | 链测试 `handoff.confirmed_used`、`disposition=ACCEPT`、`consumer.owner_confirmed_context={'USED':1}`，且行文含真实写入的正文片段 |
| 安静仍合法、tick 不是回顾配额 | 回顾选项只从"这一轮的读"产生；做法/回顾指纹只进处境（`life_step_readiness.SITUATION_KEYS` 增 3 键） | `PracticeQuietStateTest` |
| 方法不是任意执行代码 / 人格信念 | `life_practice_context.py` 只有读 + 3 个 owner 写；无任何执行/编译/调度路径；`verified_skill=False`、`stable_belief=False` 由 B 给 | `describe()["writes"]`；B 的 `NOT_VERIFIED_SKILL_NOTE` |

## 2. 生产入口真实接线

* `autonomous_life_assembly.resolve_practice_service(port, env)` 用 **B 自己的工厂** `practice_from_env(port, env)`
  在同一套 env/bindings 上建服务（含 A 的 `memory_service_from_env`）；`assemble_autonomous_life` 把它作为
  `practice_workspace=` 交给 cycle，并把结果如实报告在 `assembled["practice"]`
  （`READY` / `PRACTICE_SERVICE_NOT_IN_TREE` / `PRACTICE_FROM_ENV_FAILED` / `PRACTICE_FROM_ENV_REFUSED`）。
* cycle 侧：`self.practice = PracticeSeam(...)`；`practice_decision_context()` 进决策处境（`ctx["practice"]`）、
  `practice_writing_context()` 进真实 writer（`context={"research_context":…, "practice_context":…}`）、
  `practice_method_refs()` 作为 AG-1 的 `allowed_method_refs` 允许列表、`fingerprint_parts()` 进安静指纹、
  `_non_work_artifact_ids()` 让**做法文档**退出续写/收尾候选（回顾仍是普通可续写作品）。
* resident 未改、原实例未启用、未跑、未改 env；本轮不调用 `enable_instance_memory`（A 的入口），也没有新增配置项。

## 3. 与 A/B 固定 API 的实际差异（都在 C 侧适配，未改 A/B）

1. **B 的 `create_review` 不返回 content**：`data.project` 只有 id/title/head；C 若要"可读描述"就自己从 owner
   读（`_owner_confirmed_context` 通过 `LifeArtifactPort.read_artifact`）。
2. **B 的 `record_trial` 的 `artifact_id` 是 keyword-only**（阶段单写的是位置参数）；C 全部按关键字调用。
3. **B 的 `list_methods/list_reviews` 检索是按它自己的 haystack 子串匹配**：任务标题常常匹配不到；seam 因此做
   **至多两次**有界查询（任务 query → 空 query），仍然是 B 的检索，不新建索引。
4. **A 的 `LifeMemoryService` 在 controls DB 不存在时是 `BLOCKED / MEMORY_CONTROLS_DB_MISSING`**（不是空库）：
   链测试里按生产同款做法先 `MemoryControlLedger(...).initialize()`；没有它时回顾选项不会出现（诚实降级）。
5. **A 的 consumer 侧 `owner_confirmed_context` 结构契约**（5 键 + 引用必须在同一事件里）由 A 实现；C 只在真正
   读回该版本后才给，并额外在 AG-2 侧做结构校验（`validate_owner_confirmed_context`）。
6. **AG-2 新增 optional 字段** `LifeExperienceHandoffEvent.owner_confirmed_context`，`to_dict/from_dict` 全程保留；
   `emit_experience_handoff(owner_confirmed_context=…)` 只是传与存，不做 owner 判定。

## 4. 测试命令与退出码（本机 Windows + WSL Ubuntu 22.04 / Python 3.12）

Windows（纯逻辑部分；AG-1 与真实链在 POSIX 才可导入，`fcntl`）：

```
cd D:\nyairo-rc1-body-backend
$env:PYTHONDONTWRITEBYTECODE=1
python -m unittest tests.test_v04_practice_life_stage        # Ran 19 tests, OK (skipped=6)   exit 0
```

POSIX（WSL，真实 Supply + 真实 M0 + AG-1 原始 JSON + AG-2 + AR-0）：

```
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_v04_practice_life_stage
#   Ran 19 tests in 14.698s   OK                                        exit 0
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_v04_research_life_stage tests.test_model_continuation_no_change \
  tests.test_day_life_quiet_runner tests.test_day_life_decision_legality \
  tests.test_v04_autonomous_day_stage
#   Ran 60 tests in 11.692s   OK                                        exit 0
```

只跑了新增测试与直接受影响的既有链（续写 writer、安静 runner、决策合法性、自主日、研究阶段），没有整库回归、
没有 SHA、没有重复跑绿组、没有长时 qualification、没有真实模型/密钥。

## 5. 脚本替身（必须明示）

* `_ScriptedModelClient`：产品模型替身，固定回一段 JSON 编辑；只用 `user_prompt` 做断言。
* `_PracticeCognition`：决策模型替身，回一段**原始 JSON**，由真实 AG-1 校验；`script` 是下一次的确切答案。
* **这里没有、也不能声称真实 Gemini 的自主选择行为**：所有"她选了回顾/她提了做法/她给评价"都是脚本驱动，
  用来证明**接线与治理真实**，不证明模型倾向。

## 6. 已知剩余与代价

1. **第三次决策轮不是 CONTINUE 合法**：链测试里"反例评价"改走阶段单允许的**显式窄评价动作**
   （`PracticeSeam.record_trial`，真实 B owner 写、真实证据校验），而不是第三轮 Decision。原因是该 fixture 里
   第二个 Activity 结束后 CURRENT_ACTIVITY 候选不再 CONTINUE 合法（`_foreground_candidate` 找不到合法候选），
   属于既有 AG-1/readiness 语义，本轮未改。
2. **回顾选项只从"这一轮能读回来的经历"里取前 2 条**：没有"哪条更值得回顾"的打分（设计 §4.5 明确反对次数/分数
   自动升级）。她的问题文本仍是普通 project option 的来源之一，两条路并存。
3. **做法文档被排除在续写/收尾候选之外**：这是本轮的有意选择（做法是参考文档，不是作品）。若将来要"续写做法
   正文"，应走 B 的 `update_document`/`revise_method`，而不是 AR-0 自动续写。
4. **方法相关匹配是 B 的子串检索 + 一次空查询兜底**，不是语义检索；命中不到时按"最近 3 条"给模型看。
5. **没有 UI/手机面**（B 的所有权）；C 只把 API 语义与真实 refs 送进真实 prompt。
6. fixtures 留在 `/tmp/nyairo-v04-practice-fixtures`（WSL 内），本轮不清理。
7. 旧树里已知的三处既有失败（`test_v04_private_projects_stage` artifact 前缀断言、`test_life_wait_resume_v04`
   park basis、`test_day_life_continuity` 的 `_world_cycle(snapshot=)`）本轮未触碰、未修、也不对它们的历史归因下结论。
