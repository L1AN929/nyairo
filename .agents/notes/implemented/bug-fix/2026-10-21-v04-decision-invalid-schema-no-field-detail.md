# Agent Note: 决定被拒只留下 INVALID_SCHEMA —— 字段与规则在记录那一刻就丢了

Status: implemented

## Problem

用户指定的本地 Magpie（`http://127.0.0.1:3425/v1`、`workbuddy-wg/cn:deepseek-v4.1-flash`）真实
1 attempt：HTTP200、`finish_reason=stop`、7710ms、657 字符正文，网关能回答；AG-1 却只落
`DECISION_ATTEMPT_FAILED:INVALID_SCHEMA`、`retry_count=0`、无 committed Decision，联系侧
`DECISION_NOT_COMMITTED`、0 共同内容轮。答案能走到 schema 校验说明它是**解析成功的 JSON 对象**
（否则会是 `INVALID_JSON`），所以这是一条结构拒绝——但现场没有任何字段级证据：

保留实例 `/tmp/day-life-parent-k-091595/f-1uyn83n1/instance` 里
`life/state/agency_decision_journal.jsonl` 的 `DECISION_ATTEMPT_FAILED` 条目 payload 只有
`attempt_id/event_type/failure_class/failure_ref/model_run_ref/opportunity_id` 六个键；
`agency_decision_state.json` 的失败 attempt 同样只有类、`failure_ref` 与 `retry_count=0`。

三处丢失，全部可在代码里指出：

1. `agency_decision_ag1.validate_decision_output` 及其嵌套契约（`validate_codes`、
   `sanitize_short_rationale`、`later_item`/`future_item_actions`/`method_proposal`/`sharing`）
   约有 30 处拒绝都标 `INVALID_SCHEMA`。message 里其实写着字段，但它同时会引用模型原文
   （`"unknown keys in decision output: [...]"`、`"reason_code=..."`、`"method_ref=..."`），
   所以它从来不是一个能落盘的东西。
2. `decide` 的认知循环（改前 4422-4424 行）只留 `last_failure_class`，message 直接丢弃；
   `_record_failed_attempt`（改前 4602-4609 行）只写类、`failure_ref`、`model_run_ref`。
   于是"哪一条分支"这件事在持久化之前就没了。
3. 第二处丢失在采样侧：`ProductionAgencyCognitionAdapter` 已经算好 `parse_state`、`output_keys`、
   `output_shape`（`agency_cognition_adapter.py:1376-1403`，只有键名/长度/偏移），
   但生产装配 `autonomous_life_assembly.model_cognition`（:638）没有传 `observer`，
   而 `_observe` 在 `observer is None` 时直接返回（:1173-1176）——这些形状全部被静默丢弃。

另外两处同源缺陷：`HiddenReasoningForbiddenError` / `UniversalUtilityForbiddenError` 没有
`failure_class`，`decide` 的 `getattr(..., FAILURE_CLASS_INVALID_SCHEMA)` 会把一个
`<think>`/scratchpad 答案也标成 `INVALID_SCHEMA`；而 prompt 从没说过 `reason_codes` /
`deferred_candidate_ids` 必须是数组、也没给 `short_rationale` 的长度上限，这三条却都由
validator 以 `INVALID_SCHEMA` 拒绝。

## Decision

1. **拒绝原因落盘，且只落名字不落原文**（`agency_decision_ag1.py`）：`DecisionValidationError`
   新增 `schema_field` / `schema_rule` / `schema_extra`，取值来自两个封闭词表
   （`SCHEMA_FIELDS`、`SCHEMA_RULES`）；`describe_schema_refusal(exc)` 产出
   `{failure_class, field, rule, value_type?, unknown_key_count?, unknown_key_alias_hits?, unknown_key_shapes?}`。
   未知顶层键只记**数量、语法形状**和**命中我们自己建的 alias 词表**（`confidence`/`reasoning`/
   `notes`/…），模型写的键名与值一个都不记 —— 键名本身可能夹着私文。
2. **接入既有持久化通道**：`failure_journal_payload(...)` 生成 `DECISION_ATTEMPT_FAILED` 的
   journal payload，有 `schema_refusal` 时追加一个对象，没有时与原六键逐字一致；
   `_record_failed_attempt(..., schema_detail=...)` 用它写 journal 并回填返回字典。
   **`DecisionAttempt` 的持久化 schema 不动**（attempt 是历史契约）。
   `decide` 在拒绝处调 `describe_schema_refusal(v_exc)` 并传到失败分支。
3. **同一个答案被正确分类**：两个禁推理错误类获得自己的 `failure_class`
   （`INVALID_MODEL_OUTPUT:FORBIDDEN_HIDDEN_REASONING` /
   `...:FORBIDDEN_UNIVERSAL_UTILITY`），不再借道 `INVALID_SCHEMA`。
4. **prompt 说清楚 validator 强制的类型**（`agency_cognition_adapter.SYSTEM_PROMPT`）：
   `reason_codes` 是"一个或多个字符串的 JSON 数组、逐个来自 allowed_reason_codes"，
   `deferred_candidate_ids` 是数组、`short_rationale` 最长 `MAX_RATIONALE_CHARS`（=280，
   与 `ag1.MAX_SHORT_RATIONALE_CHARS` 由测试锁死），并把 `method_proposal` 补进
   "Answer with these keys and nothing else" 的键表。**没有放宽任何校验**：所有非法答案仍然被拒，
   失败类不变。


5. **自查修掉的三处自身问题**（父级核验后补，均已落地）：(a) 第一版编辑吃掉了
   `_record_failed_attempt` 的 `capacity_snapshot` 形参（改前基线里存在，函数体与三个调用点都还要它），
   已恢复；这正是"验证器层测试抓不到"的回归，因此新增走真实 `evaluate_opportunity` 链路的测试。
   (b) `sanitize_schema_refusal` 原来只按 `SCHEMA_DETAIL_KEYS` 复制键、不校验值，于是键名合法但值任意
   的 detail 能原样进 journal；现在**每个值都重建**（失败类必须属于本模块 17 个常量、字段/规则必须属于
   封闭词表、`value_type` 必须是七种 JSON 类型名之一、计数必须是 0..64 的整数、别名只保留自建词表里的），
   三个核心位置全是占位词时整条返回 `None`。(c) `last_schema_detail` 原来只在循环外初始化，重试时会
   把上一条答案的 `field`/`rule` 记到这一条上（制造假解释）；现在每轮开始清零。

6. **公开短理由与内部考虑的区分（2026-10-21，第二段真实验证后的定点改进）**：`short_rationale`
   的语义在 prompt 里升级为「**公开的一句理由，不是你的内部考虑**」，并给出真实留裕量：建议
   `ONE short sentence`、`<= RATIONALE_ADVICE_CHARS=80` 个字符（并明写 characters-not-tokens），
   同时保留 `MAX_SHORT_RATIONALE_CHARS=280` 作为**硬上限**（超长仍整条拒，未放宽）；`null` 明确
   写成同等合法（由模型自己产出，不做后处理改 null）；另加一段**与决定无关的**形状示例
   （`decision` 是占位符、`selected_candidate_id` 明示可 null 也可填），不偏向 START/NO_ACTION。
   真实证据：同一端点/模型/任务，窗口 `shared-practice-concise-v04` 的 1 次 attempt 里
   `short_rationale` 长度 **82 字符**并被接受、Decision 正常 commit（上一窗口同字段 >280 被拒）。

## Alternatives considered

- **最强的备选：认定 validator 太严，按 prompt 的模糊处放宽它**（`reason_codes` 接受单个字符串、
  `short_rationale` 超长就截断、未知键就丢掉）。立论很硬：模型是自然语言主体，格式抖动不该让整次
  尝试归零；旧日志也显示正文内容本身合理，放宽能立刻"变绿"。否决：那等于改写"决定必须类型正确的
  结构"这条承重契约，也等于我们自己补齐字段去凑一个决定；一旦允许字符串或截断，`INVENTED`/
  `ILLEGAL` 类拒绝的边界同时失守。正确做法是让 prompt 把类型讲清楚、让记录讲清楚拒因，两边都不动
  校验强度。
- **把 validator 的 message 原样写进日志**：立论是信息最全，连"哪个 unknown key"都在。否决：
  message 会带模型原文（键名、枚举值、`method_ref`、理由码），既可能包含私文，也直接违反"未知键
  不许原样记录"的约束。
- **给 adapter 装文件 sink 把 `output_keys`/`output_shape` 落盘**：立论是形状诊断本来就为这个准备，
  装了就能看到模型的顶层键名。否决（本轮不做）：那要新增一个存储面、一个配置项和一个新的写者，
  超出"定点修复 + 定点回归"的边界；第 1、2 条已经让既有 journal 给出 field+rule，定位够了。
- **把 `INVALID_SCHEMA` 拆成十几个失败类**：立论是一眼从类名看出原因。否决：失败类是历史契约，
  改类名会破坏既有账本与断言的可比性；字段+规则放进同一个 `schema_refusal`，既新又不破坏旧记录。

## Consequences

- **收益**：下一次真实 attempt 无论成功还是失败，`agency_decision_journal.jsonl` 的
  `DECISION_ATTEMPT_FAILED` 会多出一个 `schema_refusal`（field、rule、必要时的 value_type/
  count/alias 命中），不再只有 `INVALID_SCHEMA`；没有 detail 时 payload 与历史条目逐字一致，
  旧账本仍可比。
- **代价**：失败的 attempt 在 journal 里多一个对象；`SYSTEM_PROMPT` 从约 11.8k 增到 12330 字符
- **代价**：失败的 attempt 在 journal 里多一个对象；`SYSTEM_PROMPT` 从约 11.8k 增到 12330 字符
  （`max_prompt_chars` 限制的是 user prompt，不受影响）；`_record_failed_attempt` 多一个可选形参，
  形参表因此与改前不同（`capacity_snapshot` 保留、`schema_detail` 新增）。
- **已知上限**：这次没有观察过模型真实答案，也没有放过任何一次真实请求；`schema_refusal` 只回答
  "下一次是哪条分支"，不替代那次观测。`_reject_forbidden_dict_keys` 对
  `FORBIDDEN_DOMAIN_BODY_KEYS` 抛的是裸 `AgencyDecisionError`，`decide` 的 except 不接，
  会逃出 `decide`——不是本次观察到的类别，本次未改。
