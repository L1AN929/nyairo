# Agent Note: 决策请求的 reason_code 合法集与 validator 注册表同源，没有接线漂移

Status: implemented

## Problem

一次真实尝试被记为 `INVALID_MODEL_OUTPUT:UNKNOWN_REASON_CODE`，`field=reason_codes`、
`rule=UNREGISTERED_CODE`，未提交 Decision；具体未注册码已被安全 sanitize 丢弃，无法还原。
合法拒绝本身不是缺陷。需要回答的只有一件事：正式路径最终送给模型的
`reason_codes` 允许闭集，是否与 `DecisionReasonCodeRegistry` / `validate_codes` 使用的
集合发生了遗漏或漂移。若没有，就不能把一次随机非法输出当成接线 bug，也不能放宽校验。

## Decision

沿正式路径实核：`agency_decision_ag1` 构造的 `CognitiveDecisionRequest` 把
`allowed_reason_codes` 设为 `tuple(sorted(VALID_DECISION_REASON_CODES))`；
`ProductionAgencyCognitionAdapter.build_prompt` 原样把它放进 user prompt 的
`allowed_reason_codes`，SYSTEM_PROMPT 要求“逐字取自 allowed_reason_codes、非空数组、
不是单个字符串”；validator 的 `validate_codes` 读的正是同一个
`VALID_DECISION_REASON_CODES`（27 个，与 `DecisionReasonCodeRegistry.DESCRIPTIONS`
键集完全相等）。就“请求允许集与 validator 注册表是否接线漂移”这个问题，
**NO_CONFIRMED_DEFECT**，生产改动 0。

**2026-11-07 补测（本单）**：`tests/test_decision_reason_contract.py` 的 provider 字段此前
只来自源码阅读（scripted transport 没有执行 `profile_model_transport`），现在由
`tests/test_decision_http_chain.py` 实测补齐：真实
`AgencyDecisionService.evaluate_opportunity` 生成请求 → 真实 adapter/client →
真实 `OpenAICompatibleTransport._build_payload` 序列化出的 HTTP body 为
`model` / `messages`(system+user) / `max_tokens=4096`（`MIN_DECISION_OUTPUT_TOKENS` 的
thinking headroom）/ `temperature=0.0` / `stream=false` /
`response_format={"type":"json_object"}`，没有 `json_schema`/`response_schema` 等输出 schema。
该测试只把最后一跳 `http.client.HTTPSConnection` 换成内存连接记录请求体并返回合成 200；
没有真实 socket、DNS、server 或 provider。因此这是**本代码路径实际构造的 wire body**，
不是远端 provider 已收到的证据。

`tests/test_decision_reason_contract.py`（4 个聚合 case）覆盖：最终 system/user prompt
与注册表逐项相等；合法 START/CONTINUE/NO_ACTION 通过；未注册码仍精确
`UNKNOWN_REASON_CODE`/`reason_codes`/`UNREGISTERED_CODE`，bool、字符串、空数组、集外候选
按原规则拒绝；结构化压缩后的请求只会是注册表的子集。`tests/test_decision_http_chain.py`
（3 个聚合 case）覆盖：真实服务入口到真实 transport 的请求体与合法 NO_ACTION 提交、
合法 START 提交、未注册码的正式失败（一次 reserve、一次 wire 尝试、无自动重试、无
DecisionRecord、下游计数全 0）。

## Alternatives considered

- **认定 validator 太严、放宽合法集或把未知码映射成最近/默认码**：立论是能立刻消掉
  这次失败；否决，这会改写 `reason_codes must be registered` 的承重契约，也等于替模型
  编一个理由，任务明令禁止。
- **删掉 `_trim_reason_codes` 的压缩步骤**：立论是让请求永远携带全部 27 个码；否决，
  它是既有且有测试的容量策略（超长 prompt 时按声明顺序结构化收缩），且被裁后的集合
  仍是注册表子集，因此**请求里被广告的集合本身不含非法码**。但这只是请求侧事实：
  模型仍可能忽略 prompt 输出未注册码，裁切让模型看到的选项更少，是否间接影响了那次
  输出（以及它与失败是否有因果关系）是 UNKNOWN，不能断言“不可能”或“无因果”。改它
  还会破坏 `tests/test_lived_day_core_v04.py` 的既有 4000 字符上限断言，超出“只修实际
  请求构造”的边界。
- **在 prompt 里追加“必须选 CONTINUE”一类行为调优**：否决，合法 NO_ACTION 不能被
  禁止，且本次不是模型选得不好，而是可以自由选择的输出越界。

## Consequences

- **收益**：请求侧与 validator 的同源性被 4 个聚合用例钉住，且 3 个新聚合用例把
  “正式服务入口 → 真实 adapter/client → 真实 transport payload” 这条接缝补上；失败类、
  field/rule 和原有类型/集外拒绝一律未动；以后若有人给 prompt 或注册表加第二份枚举、
  或让 transport 多发/少发字段，这些用例会红。
- **边界**：prompt 超过 `max_prompt_chars` 时，`_trim_reason_codes` 会把广告集合缩到
  前 8 个（27→8，已在测试里实测到）。这是“比 validator 更窄”的容量行为：
  **请求侧不会因此出现非法码**，但模型仍可输出未注册码，本轮不改，也不据此推断
  与那次失败的因果。
- **覆盖差异**：旧用例的 provider 字段是源码阅读 + scripted transport；新用例实测的是
  `OpenAICompatibleTransport._build_payload` 构造的 body，但最后一跳是内存连接，
  没有真实 provider 收到或遵从该请求。两者不可互相替代。
- **未知**：那次真实尝试具体输出了哪个未注册码仍是 UNKNOWN；本测试证明的是接线一致
  与 wire body 形状，**不能保证模型以后一定遵守 prompt**，也不能把这次失败归因于
  模型、裁切或接线中的任何一方。
