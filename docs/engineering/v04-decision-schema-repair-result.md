# v0.4 决定 schema 修复：真实模型 `INVALID_SCHEMA` 的离线定因、修复与定点回归

本段是**有界离线诊断与修复**：不调用任何真实产品模型/网关，不碰原实例文件，不改全局配置，
不 stage/commit/push/deploy，不删除任何文件。下一次真实窗口由父级核验本报告后再开。

写集（只有这些）：

| 文件 | 性质 |
|---|---|
| `components/life/scripts/agency_decision_ag1.py` | 具名修复：拒因记录 + 两处误分类 |
| `components/life/scripts/agency_cognition_adapter.py` | prompt 契约与 validator 对齐（+ 一个边界常量） |
| `tests/test_decision_schema_refusal_diagnostics.py` | 新增定点回归（15 例） |
| `.agents/notes/implemented/bug-fix/2026-10-21-v04-decision-invalid-schema-no-field-detail.md` | 新增决策笔记 |
| 本文件 | 专项报告 |

**没碰**：`MANIFEST.json`、`scripts/fix_platform_world_move.py`、`scripts/fix_weixin_world_move.py`、
`.agents/continuity/current.json`、`shared_practice_life.py`、`shared_practice_service.py`、
`autonomous_life_assembly.py`、`autonomous_life_cycle.py`、`profile_model_transport.py`、
`agency_model_client.py`、任何实例/全局配置；既有 dirty 一行未回退、未整体替换。

## 1. 已确认的事实（结构证据，无正文）

保留实例 `/tmp/day-life-parent-k-091595/f-1uyn83n1/instance`（只读）：

- `life/state/agency_decision_journal.jsonl` 共 4 条：`DECISION_ATTEMPT_OPENED`、
  `COGNITION_RESPONSE_STAGED`、`DECISION_COMMITTED`、`DECISION_ATTEMPT_FAILED`。
  失败条的 `payload` 键只有
  `attempt_id / event_type / failure_class / failure_ref / model_run_ref / opportunity_id`；
  `failure_class = DECISION_ATTEMPT_FAILED:INVALID_SCHEMA`。
- `life/state/agency_decision_state.json` 里两条 attempt：一条 `SUCCEEDED`（替身接受邀请），
  一条 `FAILED`，字段只有
  `attempt_id / candidate_set_ref / completed_at / context_snapshot_ref / decision_opportunity_id /
  failure_class / failure_ref / model_run_ref / policy_version / retry_count(=0) / route /
  schema_version / started_at / status`。**没有任何字段级原因**。
- 失败 attempt 之后没有 `COGNITION_RESPONSE_STAGED`：拒绝发生在校验器，不在暂存之后。
- 实例里没有 `logs/cognition/` 之类目录，采样侧的形状观测也没有落盘。

由此能确定的只有：这条 657 字符的答案**没有**走 `INVALID_JSON` 分支
（`agency_cognition_adapter.py:1368-1373` → `agency_decision_ag1.py:4640-4645`），而是进了
`validate_decision_output` 的 schema 拒绝；"拒在哪一个字段、违反哪条规则"在写盘前就丢了。

**不能由该类反推答案形状**：`INVALID_SCHEMA` 至少还有两条来源——顶层不是映射（JSON 数组也会
命中 `agency_decision_ag1.py:3000`）、以及改前没有自己失败类的禁推理错误（`<think>`/scratchpad，
缺陷 C）。所以"它一定是个合法 JSON 对象"只是其中一支；`finish_reason=stop` 与 657 字符只说明
内容完整返回，**不说明结构**。本段不据此推断。

## 2. 已确认缺陷（含代码行）

### 缺陷 A：拒因在持久化之前就丢（本次主缺陷）

- `agency_decision_ag1.py:4661-4670`（改前 4422-4424）`decide` 的认知循环：
  `except (... DecisionValidationError, HiddenReasoningForbiddenError, UniversalUtilityForbiddenError)`
  只留 `last_failure_class`，`v_exc` 的 message 直接丢弃。
- `agency_decision_ag1.py:4801-4877`（改前 4556-4617）`_record_failed_attempt`：
  journal payload 只有 `event_type / opportunity_id / attempt_id / failure_class / failure_ref /
  model_run_ref`，返回字典只有 `status / opportunity / decision_record / attempt / failure_class`。
- 量级：`validate_decision_output` 及其嵌套契约里约 30 处拒绝都写
  `FAILURE_CLASS_INVALID_SCHEMA`（`agency_decision_ag1.py` 现 3001/3036/3047/3072/3098/3275/3287
  + `validate_codes` 516-537 + `sanitize_short_rationale` 968/980 + `validate_method_proposal` 1063-1149
  与 `validate_sharing_action` 各自十余处）。所以"看到 INVALID_SCHEMA"等价于"看到 30 个可能里
  的一个，但不知道是哪个"。
- message 为什么不能直接落盘：它引用模型原文（`"unknown keys in decision output: [...]"`、
  `"unregistered reason_code=..."`、`"method_ref=... was not offered"`），键名/值都可能是私文。

### 缺陷 B：采样侧的安全形状诊断没有接收方

- `agency_cognition_adapter.py:1376-1403`（改前 1376-1403）已经算出 `parse_state`、
  `output_keys`（顶层键名排序）、`output_shape`（`content_chars`/`fenced`/`json_parsed`/
  `parse_error_offset`/`finish_reason`/`reasoning_*`/`max_output_tokens_sent`）。
- 但 `_observe`（`agency_cognition_adapter.py:1173-1182`）在 `observer is None` 时直接返回，
  而生产装配 `autonomous_life_assembly.py:638-639` 构造 `ProductionAgencyCognitionAdapter`
  时**没有传 `observer`**。所以这些形状在生产路径上从未被任何 sink 接收。

### 缺陷 C：两个禁推理错误被误标成 schema 形状错误

- `HiddenReasoningForbiddenError` / `UniversalUtilityForbiddenError` 原本没有 `failure_class`，
  `decide` 的 `getattr(v_exc, "failure_class", FAILURE_CLASS_INVALID_SCHEMA)` 把它们也记成
  `INVALID_SCHEMA`。一个 `<think>`/scratchpad 答案（`sanitize_short_rationale:977`）会被报成
  "schema 形状错误"。

### 缺陷 D：prompt 与 validator 的契约不对齐（可静态验证，不是猜测）

- `reason_codes`：`validate_codes`（519-537）要求**已注册字符串的列表**（单个字符串 →
  `INVALID_SCHEMA`）；改前 prompt 只说"必须来自 allowed_reason_codes"，从未说"数组"。
- `deferred_candidate_ids`：`agency_decision_ag1.py:3083-3098` 要求列表（字符串 →
  `INVALID_SCHEMA`）；改前 prompt 从未说类型。
- `short_rationale`：`sanitize_short_rationale:968/980` 要求字符串且 ≤
  `MAX_SHORT_RATIONALE_CHARS=280`（超长 → `INVALID_SCHEMA`）；改前 prompt 给了所有可选字段的上限
  （`<=200 chars` 等），**唯独没给这两个必填文本字段的上限**。
- `method_proposal`：validator 允许（`agency_decision_ag1.py:3029`），prompt 也在 217-225 行描述它，
  但 prompt 自己的 "Answer with these keys and nothing else" 键表漏了它。

## 3. 归入该类、但尚未确定的分支（不排序，也不猜）

`INVALID_SCHEMA` 覆盖本模块约 30 处拒绝。下面每一条都会落到同一个类，现场只有这个类、没有该答案的
结构记录，**没有任何依据给它们排序**：顶层不是映射（JSON 数组也算）、未知顶层键、
`decision`/`selected_candidate_id`/`reason_codes`/`deferred_candidate_ids`/`short_rationale`
类型不符、`short_rationale` 超长、`later_item`/`future_item_actions`/`method_proposal`/`sharing`
被各自域拒、以及改前没有自己失败类的禁推理错误。第 4 节的修复让下一次尝试直接给出
`field`+`rule`，**那时**才谈得上判断是哪一条。

## 4. 修复（修改前后作用）

### 4.1 `components/life/scripts/agency_decision_ag1.py`

| 位置（改后行） | 改前 | 改后 |
|---|---|---|
| 310-315 | 无 | 新增 `FAILURE_CLASS_FORBIDDEN_HIDDEN_REASONING` / `..._UNIVERSAL_UTILITY` |
| 633-646 | 两个禁推理错误无 `failure_class` | 各自带自己的失败类（缺陷 C） |
| 651-663 | `DecisionValidationError(message, failure_class=...)` | 追加 `schema_field` / `schema_rule` / `schema_extra` 三个**值无关**属性 |
| 667-721 | 无 | 封闭词表 `SCHEMA_FIELDS`（12 个字段名）、`SCHEMA_RULES`（10 个规则）、`SCHEMA_ALIAS_KEYS`（20 个我们自建的别名）、`SCHEMA_DETAIL_KEYS`（可持久化键白名单） |
| 729-748 | 无 | `SCHEMA_DETAIL_FAILURE_CLASSES`（本模块 17 个失败类枚举）、`SCHEMA_DETAIL_UNKNOWN`、`SCHEMA_VALUE_TYPES`、`SCHEMA_DETAIL_MAX_COUNT`、`SCHEMA_FIELD_UNKNOWN` |
| 777-799 | 无 | `describe_schema_refusal(exc)`：只从异常属性取原始值，交 `sanitize_schema_refusal` 复核 |
| 801-867 | 无 | `sanitize_schema_refusal(detail)`：**逐值**重建（见 4.3），全是固定词时返回 `None` |
| 869-897 | 无 | `failure_journal_payload(...)`：无 detail 时与原六键**逐字一致**，有 detail 时追加一个 `schema_refusal` |
| 519-537 | `reason_codes` 三处拒绝无字段信息 | 标 `reason_codes` + `NOT_A_LIST`/`UNREGISTERED_CODE`/`EMPTY_NOT_ALLOWED`（含 `value_type`） |
| 966-981 | `short_rationale` 两处拒绝无字段信息 | 标 `short_rationale` + `MISSING_OR_WRONG_TYPE`/`TOO_LONG` |
| 2998-3048 | 顶层三处（非对象/未知键/decision） | 标 `decision_output`/`decision`；未知键只记**数量+语法形状+命中自建别名** |
| 3070-3098 | `selected_candidate_id`/`deferred_candidate_ids` | 标字段 + `MISSING_OR_WRONG_TYPE`/`NOT_A_LIST` + `value_type` |
| 3272-3291 | `later_item`/`future_item_actions` 包装 | 标 `REFUSED_BY_DOMAIN` |
| 3313-3353 | `method_trial`/`method_proposal`/`sharing` | 用 `_annotate_schema_field` 补字段名 |
| 4872-4955 | `_record_failed_attempt` 只有类；且**本段第一版误删了 `capacity_snapshot` 形参** | 恢复该形参 + 新增 `schema_detail`；journal 用 `failure_journal_payload`；返回字典在有 detail 时带 `schema_refusal`（4.3） |
| 4676-4760 | 拒绝处只留类；重试可能沿用上一次拒因 | 每轮 `last_schema_detail = None`；`last_schema_detail = describe_schema_refusal(v_exc)`，失败分支传入 |
| 5614-5615 | — | 两个新失败类加入 `__all__` |

**没有动的**：`DecisionAttempt` 持久化 schema、失败类取值、重试策略（`max_schema_retries` 仍只允许 0/1、
仍只重试 schema/JSON 类）、`INVENTED_*`/`ILLEGAL_*`/`ATOMIC`/`HARD_CONSTRAINT` 判定、候选合法性、
`decision_output_cap()` 的 512/700/2048 上限（无证据说不存在不足）、任何权限/CAS/新鲜度语义。
形参确有变化（`_record_failed_attempt` 增 `schema_detail`；`capacity_snapshot` 的误删与恢复见 4.3）。

### 4.2 `components/life/scripts/agency_cognition_adapter.py`

| 位置（改后行） | 改前 | 改后 |
|---|---|---|
| 87 | 无 | `MAX_RATIONALE_CHARS = 280`（与 `ag1.MAX_SHORT_RATIONALE_CHARS` 由测试锁死） |
| 176-188 | 键表漏 `method_proposal`；`reason_codes`/`deferred_candidate_ids` 无类型；`short_rationale` 无上限 | 明确"JSON 数组、逐个来自 allowed_reason_codes、不得是单个字符串、不得为空数组"、"deferred 是数组或 null"、"short_rationale ≤280 字符或 null"，并把 `method_proposal` 写进允许键表 |
| 249-253 | 常量字符串 | 用 `.replace("__MAX_RATIONALE_CHARS__", str(MAX_RATIONALE_CHARS))` 注入真实上限 |

解析入口一行未改：`unwrap_fenced_json` 仍然只拆"整段就是一段 fence"，散文包着 fence 仍交 AG-1 拒；
不做 markdown 提取、不做宽松 JSON、不补造 Decision、不强制 accept。

### 4.3 本段自查发现并修掉的自身问题（父级核验后补）

**（a）我自己删掉的形参。** 第一版编辑 `_record_failed_attempt` 时，替换 docstring 的那一步吃掉了
签名里的 `capacity_snapshot: AgencyCapacitySnapshot,`（改前在 4559 段，现 4878 行），而函数体仍然
使用它、三个调用点仍然传它。**这是本段引入的回归，不是历史问题**：修前基线里该形参存在
（`phase1/prefix` 对比可见），运行时的表现是任何一次失败尝试在 `commit_delta` 之前抛
`TypeError: _record_failed_attempt() got an unexpected keyword argument 'capacity_snapshot'`，
也就是"失败尝试连失败都记不下来"。已恢复该形参，并新增走真实 `evaluate_opportunity` 的测试
（4.2 的"真实服务层"）——验证器层测试抓不到它，因为验证器看不到这个调用。

**（b）`sanitize_schema_refusal` 原来只校验键名、不校验值。** 旧实现按 `SCHEMA_DETAIL_KEYS` 复制，
于是 `{"field": "<某人的话>"}` 这种**键名合法、值任意**的 detail 能原样进 journal。现在**每个值都重建**：

- `failure_class` 必须属于本模块 17 个失败类常量，否则写成 `UNKNOWN`；
- `field` / `rule` 必须属于封闭词表，否则 `unknown` / `UNCLASSIFIED`；
- `value_type` 必须是七种 JSON 类型名之一，否则丢弃；
- `unknown_key_count` / `unknown_key_shapes` 的值必须是 `0..64` 的整数（`bool` 不算）；
- `unknown_key_alias_hits` 只保留 `SCHEMA_ALIAS_KEYS` 里的名字；
- 三个核心位置全是占位词时**整条返回 `None`**（不写"什么都没查出来"的空壳）。

**（c）重试会串用上一条答案的拒因。** `last_schema_detail` 在循环外只初始化一次，若第一次是
`INVALID_SCHEMA`、第二次是 `INVALID_JSON`，journal 会把第一次的 `field`/`rule` 记在第二次上
（等于制造一条假解释）。现在**每轮开始清零**，只报该轮自己的答案。


## 5. 验证（真实命令、真实退出码）

环境：WSL Ubuntu + `python3.12`、`PYTHONDONTWRITEBYTECODE=1 python3 -B`；脚本与日志在会话 scratch
`…/phase1/`（本轮新日志为 `log2-*`，上一轮 `log-*` 保留未覆盖）。

| # | 命令 | 真实退出码 | 结果 |
|---|---|---|---|
| 1 | `python3 -B tests/test_decision_schema_refusal_diagnostics.py` | **0** | Ran **20** tests, OK（日志 `phase1/log2-schema_refusal.txt`） |
| 2 | `python3 -B tests/test_cognition_thinking_response.py` | **0** | Ran 7 tests, OK |
| 3 | `python3 -B tests/test_day_life_decision_legality.py` | **0** | Ran 14 tests, OK |
| 4 | **行为负控 A**：`python3 -B phase1/prefix_negative_control_decide.py <repo> <fixture>` | **0**（脚本自身；用例层面 failures=1/errors=1） | 把**修前** `_record_failed_attempt` 方法体（scratch 基线 `git show HEAD:`+保存的 worktree diff，AST 取源）装回活类，跑真实 `evaluate_opportunity`：Ran 2, failures=1, errors=1 —— `KeyError: 'schema_refusal'` 与 `AssertionError: 'schema_refusal' not found in <journal>`。**红在缺少持久拒因，不是 import/AttributeError** |
| 5 | **行为负控 B**：同上脚本第二例 | **0**（脚本自身；TypeError 已复现） | 装回"签名缺 `capacity_snapshot`"的形状，真实 decide 立刻 `TypeError: … got an unexpected keyword argument 'capacity_snapshot'` —— 证明该回归只能被走 decide 的测试抓住 |
| 6 | **模块级修前对照**：`python3 -B phase1/prefix_negative_control.py <repo>` | **1** | run=20, failures=2, errors=21（修前两个模块整替身；只看"信息缺失"这一点，不作为主负控） |
| 7 | `node --experimental-strip-types …\verify-agent-note-tree.ts`（repo cwd） | **0** | ok: 80 note(s) tree and relative links verified |
| 8 | `node --experimental-strip-types …\verify-agent-note-format.ts`（repo cwd） | **0** | ok: 80 note(s) verified |
| 9 | `git diff --check` | **0** | 无空白错误 |
| 10 | `python3 -B tests/test_life_v1_restart.py` | **1** | Ran 13 tests, **3 failures** —— 见下（不计入本段绿） |

新测试覆盖两层。**验证器层**：合法答案仍被接受；未知顶层键（含 `SECRET_KEY`/`SECRET_VALUE` 哨兵）
被具名拒绝且记录中**不出现**键名与值；`decision`/`selected_candidate_id`/`deferred_candidate_ids`/
`reason_codes`/`short_rationale` 五处类型错误各自具名（含 `value_type`）；`short_rationale`
超长 → `TOO_LONG`；`reason_codes` 空/未注册 → `EMPTY_NOT_ALLOWED`/`UNREGISTERED_CODE`；
`later_item`/`method_proposal`/`sharing` 具名；"旧式 message-only 异常"无法具名（负控原理）；
禁推理有了自己的失败类；prompt 说出了它强制的类型与上限，且 `method_proposal` 在允许键表里。
**真实服务层**（`AgencyDecisionService.evaluate_opportunity`，真 store/真 lease/真签名 capability +
替身 cognition）：失败 attempt 的 `schema_refusal` 真的进了 `agency_decision_journal.jsonl`；
`decision_record` 仍为 `None`、`decisions_by_id` 仍为空；持久化 attempt 记录**没有**多出字段；
答案里的哨兵键/值在 journal 文件里**一次都没出现**；恶意 detail 的值只能变成固定词或整条不写；
重试后落到 `INVALID_JSON` 时**不带**上一次的拒因，落到第二次 schema 拒绝时只报第二次的
`field`/`rule`/`value_type`。

**第 10 项的诚实说明**：失败的三例是 `test_artifact_choice_commits_a_real_supply_version`（两个类各一次）
与 `test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`，断言内容都是 AR0
closeout 的 `ALREADY_DONE` vs `ACTED`（`action_reality`/`managed_artifact` 域），本次写集不含该域。
父级已在 `docs/engineering/v04-shared-practice-parent-result.md` §7 记录同族红例
（`tests.test_life_v1_integration` 7 fail + 1 error，AR0 closeout/continuity）。这**只是相关性说明，
不是 baseline 证明**：该套件是跨进程重启 harness，无法做等价修前基线，所以我没有把它算作本段绿，
也没有把它命名为"pre-existing 与本次无关"。

## 6. 真实验证需要什么、安全诊断已就绪到什么程度

下一次有界真实 attempt（父级另行开窗）不需要新的取证脚本：

1. 失败时实例内 `life/state/agency_decision_journal.jsonl` 的 `DECISION_ATTEMPT_FAILED` 条目会带
   `schema_refusal: {failure_class, field, rule, value_type?, unknown_key_count?, unknown_key_alias_hits?, unknown_key_shapes?}`。
   读法：`field` 指出字段（`decision_output` / `decision` / `selected_candidate_id` /
   `reason_codes` / `short_rationale` / `deferred_candidate_ids` / `later_item` /
   `future_item_actions` / `method_ref` / `method_trial` / `method_proposal` / `sharing` /
   `unknown`），`rule` 指出规则；未知键只给数量与别名命中（`confidence`/`reasoning`/`notes`…），
   不给模型原文。
   说明：`schema_refusal` 是**该次 attempt 最后一条答案**的拒因（重试不串用，见 4.3c），且里面的每个值
   都是固定枚举词（失败类常量 / 字段名 / 规则名 / 七种 JSON 类型名 / 0..64 计数 / 我们自建的别名）；
   来源不明的值只会变成 `UNKNOWN`/`unknown`/`UNCLASSIFIED` 或被丢弃，整条无内容时干脆不写。
2. 判别表：`field=decision_output, rule=UNKNOWN_TOP_LEVEL_KEYS` → 模型自加了键（prompt 已明确禁止，
   属模型不遵守）；`field=reason_codes, rule=NOT_A_LIST` → 改前是 prompt 契约缺口，改后仍出现则
   属模型不遵守；`rule=TOO_LONG` → 同一判断；`rule=REFUSED_BY_DOMAIN` → 嵌套域契约问题，需另查。
3. 仍然**没有**的东西（不要假装有）：模型真实答案正文、顶层键名、prompt 实际字节。
   采样侧的形状诊断（`output_keys`/`output_shape`）依旧没有生产者 sink —— 要拿到它必须新增一个
   存储面/配置项并授权，本段刻意未做（见笔记 Alternatives 第 3 条）。

## 7. 未闭合与边界

- 本次**0 次真实模型/网关调用**、0 个 live 产品进程；`tests/test_cognition_thinking_response.py`
  用的是 127.0.0.1 临时端口的 loopback 替身；`tests/test_life_v1_restart.py` 会起本地子进程
  （无模型调用），跑完 `ps` 已确认无残留。
- 原实例 `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094` 未读写；`/tmp/day-life-parent-k-091595`
  只读。
- 相邻未修：`_reject_forbidden_dict_keys`（`agency_decision_ag1.py:1001`）对 `FORBIDDEN_DOMAIN_BODY_KEYS`
  （:1013-1015）抛的是裸 `AgencyDecisionError`，`decide` 的 except 不接，会逃出 `decide`；
  它不是本次观察到的类别，改动它会换掉调用方的错误面，留着由父级决定。
- 仍然没有：自然共作成功、模型资格 GO、24h/多日自治、跨排队锁版本、远程外发。

## 8. 真实窗口结果（2026-10-21，stage `shared-practice-schema-repair-v04`）

预检（不含 completion、未开窗口）：`GET /models` → HTTP200、精确 model id 在列、ledger `NOT_STARTED / 0 attempts`。

**唯一一次真实 attempt（1/12）**：HTTP200、`finish_reason=stop`、7423 ms、正文 645 字符、推理 2974 字符。
AG-1 拒绝，而这次**有名字**——实例 `/tmp/day-life-parent-s-574943` 的
`life/state/agency_decision_journal.jsonl` 里 `DECISION_ATTEMPT_FAILED.payload.schema_refusal`：

| 键 | 值 |
|---|---|
| `failure_class` | `DECISION_ATTEMPT_FAILED:INVALID_SCHEMA` |
| `field` | `short_rationale` |
| `rule` | `TOO_LONG` |

这就是此前四次真实窗口一直缺失的那一步：**字段级定因**。拒绝发生在 `short_rationale` 超过
`MAX_SHORT_RATIONALE_CHARS=280`；不是未知键、不是类型错、不是坏 JSON、也不是截断。

**这是模型不遵守已说明的界，不是产品契约缺陷**：本轮模型收到的 SYSTEM_PROMPT 已经写着
"short_rationale: a plain string of at most 280 characters, or null"（本轮前实测
`'280 characters' in SYSTEM_PROMPT` 为 True，`MAX_RATIONALE_CHARS == ag1.MAX_SHORT_RATIONALE_CHARS == 280`）。
契约讲清了、校验器没错，模型仍写了更长的理由。

**没有采用的三条路（都会把问题掩盖）**：① 把 280 调大——等于把"理由必须有界"改成迁就这一次输出，
换个字段下次照样失败；② 超长就截断——等于我们替模型改写答案，而且"记录的东西"不再等于"模型说的
东西"；③ 在同一账本里再试——账本已 `ENDED_NO_FURTHER_CALLS`，回拨就是洗绿。

**后果（诚实记录）**：邀请 `outcome=UNKNOWN`、`reason_code=DECISION_NOT_COMMITTED`、无 committed
Decision、无 joint Activity、practice 未打开、0 轮共作内容；原私人 Activity 仍 ACTIVE / page1，
checkpoint 未变（`original_checkpoint_preserved=true`、`invitation_does_not_advance_private_reading=true`）。

**账与资源**：stage ledger 用 1/12、墙钟 13.2 s / 900 s、`ENDED_NO_FURTHER_CALLS`；实例自身 request
ledger 0→1；五项会计（attempt 编号唯一、每个 transport 结果都有预留、bridge 与持久预算一致、
实例预留增量 = HTTP 次数、≤12）全真；fixture `/tmp/day-life-parent-s-574943` 全部保留；自有资源关闭；
无残留进程；prompt / raw 答案 / 推理一律未落盘（bridge 只写枚举、计数与长度）。

**这次窗口证明了什么、没证明什么**：证明了 §4 的修复在真实链路上生效（`INVALID_SCHEMA` 不再是黑箱），
也证明了 657 字符那条答案不是"坏 JSON"；**没有**证明自然共作链——邀请就被模型的超长理由挡在门外。
剩余 11 次额度已随 `finally` 永久 ENDED，不重开、不改账本；要继续必须由父级另开一个新的有界窗口。

证据（scratch，均无 raw/prompt/推理）：`shared-practice-schema-repair-preflight.json`、
`shared-practice-schema-repair-model-budget.json`、`shared-practice-schema-repair-model-attempt-1.json`、
`phase2-run1.log`、`shared-practice-schema-repair-phase2-1792549514167574943.json`（唯一 phase-2 结构结果：
journal 安全快照、budget/status、实例会计、stop point 与未到达项）。

## 9. 输出简短约束的定点改进与第二个真实窗口（2026-10-21，stage `shared-practice-concise-v04`）

### 9.1 产品改动（只改 prompt，不动校验）

`agency_cognition_adapter.py`：

| 位置 | 改动 |
|---|---|
| `:92` | 新增 `RATIONALE_ADVICE_CHARS = 80`（**建议值**，与强制上限分离；`MAX_RATIONALE_CHARS=280` 原样保留） |
| `:191-199` | `short_rationale` 子条：明确"**公开的一句理由，不是你的内部考虑**"——思考、权衡、只想对自己说的话都不进答案；`ONE short sentence`、`aim for <= 80 characters (characters, not tokens)`；`280 characters is the hard ceiling`（超长仍整条拒）；`null is equally legal`（由模型自己产出，不做后处理）；"nothing in this prompt weighs one legal decision word over another" |
| `:200-203` | 新增**与决定无关**的形状示例：`decision` 是 `<one of the legal decision words>` 占位符，`selected_candidate_id` 明写 `<candidate_id, or null>`，并说明"null 与填值都是合法形状" |
| `:266-270` | 两个数字用 `.replace()` 注入（`__MAX_RATIONALE_CHARS__`、`__RATIONALE_ADVICE_CHARS__`），提示里不残留占位符 |

**没动的**：`MAX_SHORT_RATIONALE_CHARS=280`、strict parse、失败类、`decision_output_cap`、任何
`situation_context` 块与"没有日程、不欠输出、NO_ACTION 合法"的自主语义（有测试守住）。

### 9.2 离线验证（先红后绿）

| 项 | 命令 | 真实 exit | 结果 |
|---|---|---|---|
| 定点套件 | `python3 -B tests/test_decision_schema_refusal_diagnostics.py` | **0** | Ran **24**, OK（新增 4 例） |
| 相关既有 | `python3 -B tests/test_cognition_thinking_response.py` | **0** | Ran 7, OK |
| 全链替身 | `python3 -B tests/test_v04_shared_practice_life_chain.py` | **0** | Ran 3, OK |
| **旧 prompt 负控** | `python3 -B phase3/old_prompt_control.py`（AST 取基线 `SYSTEM_PROMPT` 原文，跑同一组断言） | **0**（= RED_AS_EXPECTED） | 8 条新要求断言在旧 prompt 上**全 false**；2 条守卫断言（生活 context、占位符已解析）旧 prompt 上已 true |

新增断言：`test_the_rationale_boundary_is_still_280_strict`（**280 通过 / 281 被拒**，`field=short_rationale`、
`rule=TOO_LONG`，证明边界没被放宽）、`test_the_prompt_asks_for_a_short_public_reason_and_says_what_it_is_not`、
`test_the_prompt_leaves_null_and_the_decision_choice_to_the_model`（形状示例里不得出现任何具体决定词）、
`test_the_prompt_still_carries_the_normal_life_context_and_her_autonomy`。

### 9.3 真实窗口（1 次 attempt，`shared-practice-concise-v04`）

预检 `GET /models` HTTP200、model 在列、0 completion、ledger `NOT_STARTED`。随后 **1 次**真实 attempt：
HTTP200 / `stop` / 26.7 s 整轮 / Decision **正常 commit**（`dec:1fc362665f79e6b4`），
`short_rationale` 长度 **82 字符**、**被接受**——上一个窗口同一字段 >280 被整条拒绝。这是本轮改动的
直接效果：**不再有 INVALID_SCHEMA**。

模型的决定是 **CONTINUE**（继续读她自己的材料页 1），reason_codes
`CURRENT_ACTIVITY_CONTINUITY / CONTINUITY_PREFERRED / CAPACITY_UNKNOWN_DEFERRED / USER_REQUEST_DEFERRED`，
邀请候选在 `deferred_candidate_refs` 里；按产品语义"任何别的答案都是拒绝"，所以邀请 `DECLINED`、
joint 未开始、practice 未打开、0 轮共作内容。原 Activity 仍 ACTIVE / page1、checkpoint 未变。

**这是一次合法的不接受，我尊重它**：没有重放同一邀请、没有换个说法刷接受、没有改 prompt 去偏向
START。链上后续（3 轮真实交流 → END → fresh RESUME page2 → 重启读回）因此**未到达**。

窗口账：attempts **1/12**、墙钟约 173 s / 900 s、`ENDED_NO_FURTHER_CALLS`（owner 显式关闭，不是
"预算耗尽"）；实例自身 request ledger 0→1；bridge 与持久预算一致；fixture
`/tmp/day-life-parent-c-029002` 保留；自有资源关闭、无残留进程；prompt/raw/推理一律未落盘。

### 9.4 两处诚实更正

1. **上一跑报出的 `FAILED_NO_COMMITTED_DECISION` 是我的 harness 判断错**：`invitation_evidence`
   收到的是 `fx.data['subject_id']`（`test:w05`），而 AG-1 DecisionRecord 的 subject 是
   `chiyo.global`，于是"已 commit"被读成"未 commit"。从保留实例只读复核（`phase3/reclassify_r1.py`，
   无模型调用、无产品改动）后结论改为 **`VALID_NONACCEPTANCE`**。
2. **观察到但本轮不改**：被拒邀请的 contact 项里 `joint_activity_ref` 填的是她**正在读**的 Activity
   （`life_co_presence.py:908` `joint_ref = getattr(integration, "activity_ref", None)`），字段名与
   "joint"语义不符。实际效果是对的（session 未 OPEN、无 `FINITE_CO_PRESENCE` Activity、无内容写入），
   属证据字段命名问题；改它会动 contact 端口契约，超出本轮定点范围，留给父级定。

### 9.5 结论与真正剩余阻塞

- **已解决**：`short_rationale` 不再因为长度被拒（82 字符、被接受、决定正常 commit）。
- **未证明**：自然共作链。剩下的阻塞不是契约、不是解析、不是我在代码里能修的：**是否接受邀请由模型
  自己判断**，这次它选择继续读自己的材料。要拿到 START 只能等它自己接受，或换一个允许它的模型；
  两者都不该靠改 prompt 偏置或重放同一事件来制造。
