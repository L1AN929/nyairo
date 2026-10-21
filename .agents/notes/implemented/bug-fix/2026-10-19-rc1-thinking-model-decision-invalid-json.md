# Agent Note: 真实 thinking 模型的决策被判 INVALID_JSON —— 真因是思考 token 吃掉了输出上限

Status: implemented

## Problem

原实例（同 root、一次真实 tick、`run:one-day`、无 injected factory）的真实结果是
`model_calls=1`、`DECISION_FAILED DECISION_ATTEMPT_FAILED:INVALID_JSON`、`actions=0`、
`owner_confirmed_versions=0`。`INVALID_JSON` 看着像"模型吐了坏 JSON"，但代码链路上这个词有三个来源，
其中一个是假警报：

1. `profile_model_transport.finish_send` 只读 `choices[0].message.content`；`content` 为空时抛
   `PROVIDER_MALFORMED_RESPONSE("empty completion")`（改前 214-215 行）。
2. `agency_cognition_adapter` 把 `PROVIDER_MALFORMED_RESPONSE` 直接映射成
   `ag1.FAILURE_CLASS_INVALID_JSON`（改前 356 行）。于是"答案还没开始就被截断"被报成"模型输出了
   不可解析的 JSON"。
3. 真的 `json.loads` 失败（改前 428-429 行把原文交给 AG-1，由 AG-1 3885-3891 报 INVALID_JSON）。

而被私有绑定的 `cn:deepseek-v4.1-flash` 是 reasoning 模型：`scratch/original-model-smoke-*.json`
的真实 usage 里 `completion_tokens=34` 内含 `completion_tokens_details.reasoning_tokens=31`，即
**思考 token 记在 `completion_tokens` 里、和答案共用同一个 `max_tokens`**。决策请求只给 512
（`autonomous_life_assembly.py:456,458` → `profile_model_transport.py:125`），候选 16 个、提示最长
12000 字的场景下思考一遍就把预算用光，`content` 回来是空串、`finish_reason="length"` —— 落到上面第 1+2
条，正好产出观察到的那个词。`reasoning_content` 里哪怕真的写着一个完整 decision，也绝不能被当成答案。

## Decision

1. 传输层把"200 但没有答案文本"拆成三种诚实失败类（`agency_model_client.py:54-59`）：
   `PROVIDER_OUTPUT_TRUNCATED`（`finish_reason=="length"`）/`PROVIDER_REASONING_ONLY`/
   `PROVIDER_EMPTY_CONTENT`；`reasoning_content` 只量长度并丢弃，永不进 `text`。非 auth/rate/5xx 的 4xx
   改判 `PROVIDER_REQUEST_REJECTED`（请求本身被拒，不是模型输出坏）。`finish_send` 还额外报告
   `finish_reason`/`reasoning_chars`/`content_chars`（只有形状，没有正文）。
2. 适配器给思考留出上限：`MIN_DECISION_OUTPUT_TOKENS = 2048`
   （`agency_cognition_adapter.py:78`，`decision_output_cap()` 280 行），实际发出去的 `max_tokens`
   取 `max(configured, request.max_output_tokens, 2048)`；配置值本身不变，只是线上天花板抬高。**这是
   上限不是花费**：每请求预留、`settle()`、usage 台账一字未改。
3. 新失败类各有自己的 `failure_class`（445-448 行），不再借道 `INVALID_JSON`。
4. 严格 JSON 入口保持严格：只在整个回复就是"一段 fence 且没有别的字"时拆 fence
   （`unwrap_fenced_json()` 296 行）；散文包着 fence 原样交给 AG-1，依旧 `INVALID_JSON`。
5. `cognition_result` 观测新增 `output_shape`（539 行）：`content_chars`/`fenced`/`json_parsed`/
   `parse_error_offset`/`finish_reason`/`reasoning_present`/`reasoning_chars`/`max_output_tokens_sent`，
   全是形状与计数，没有正文与私文。

## Alternatives considered

- **把 `reasoning_content` 当答案解析出来（哪怕是完整 JSON）**：最强立论是"模型确实想清楚了、内容也在，
  只是放错字段，读出来最省事"。否决：那等于从非答案字段里猜 decision，正是 §14 禁止的自由文本解析；
  reasoning 是推导不是承诺，一旦允许，模型可以"想"一个 action 而"答"什么都没写，权威边界就破了。
- **把 `INVALID_JSON` 全部放宽成"任意包含 JSON 的文本都提取"**：立论是"反正后面 AG-1 还会严格校验
  candidate/enum/reason code"。否决：把解析责任从"决定入口"挪到"校验器"，等于用一个宽松解析器替换
  一个严格入口，且 fence 之外的散文照样是不可信输入。
- **把超时从 8s/30s 抬到 120s 或加一次重试**：立论是"多给点时间就不截断了"。否决：截断是 token 上限
  不是时间；重试还会破坏"每 epoch 最多一次付费调用"和每请求预留不返还的铁律。
- **只改 `autonomous_life_assembly.py` 里的 512 字面量**：立论是"最小改动"。否决：那只是把同一颗雷换到别处
  （reading/artifact 两条路径同样 512），且适配器仍会把截断误报成 INVALID_JSON。

## Consequences

- **收益**：思考预算不再吃答案；`INVALID_JSON` 重新只表示"模型输出确实不是 JSON"；下一次原实例 run 的
  阻塞原因能一眼分清是截断、只有推理、空回复还是请求被拒。
- **代价与已知上限**：单次请求的 `max_tokens` 天花板从 512 抬到 2048，实际花费仍由模型自己停止决定；
  4xx 分类变化会让某些被测断言（原本断言 INVALID_JSON 的）需要跟着改；若网关真的不支持
  `response_format`，现在会明确报 `MODEL_REQUEST_REJECTED` 而不是假装是模型的问题——修复它需要另一次
  有界复验，不能靠放宽 JSON 入口来"变绿"。
