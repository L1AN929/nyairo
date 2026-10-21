# rc1 真实模型决策 `INVALID_JSON`：定点修复结果（LPC0B-R3）

写集（只碰这些）：

- `components/life/scripts/profile_model_transport.py`
- `components/life/scripts/agency_cognition_adapter.py`
- `components/life/scripts/agency_model_client.py`
- 新增 `tests/test_cognition_thinking_response.py`
- 本文件 + note `.agents/notes/implemented/bug-fix/2026-10-19-rc1-thinking-model-decision-invalid-json.md`

**没碰**：`autonomous_life_cycle.py`、`autonomous_life_runner.py`、C/W/R 三进程、owner/phone/World/
Supply 的任何模块、`MANIFEST.json`；没有 stage/commit/push，没有跑真实模型，没有动 provider 绑定，
没有改原实例（父级两进程 gen2 未触碰）。

## 1. 结论

原实例首轮 `DECISION_FAILED DECISION_ATTEMPT_FAILED:INVALID_JSON` 没有保留响应形状，不能仅凭计数器确定其唯一原因。代码确认存在“thinking耗尽512输出上限→空content被映射为INVALID_JSON”的缺陷；父级后续同模型结构对照已真实复现该路径：512时length/content0/reasoning_tokens512，2048时stop/JSON对象。它支持本次定点修复，不倒写首轮未观察的响应事实。

## 2. 证据链（全部来自代码与安全结构证据，未读任何私文/密钥/提示词正文）

| # | 事实 | 出处 |
|---|---|---|
| 1 | 绑定模型把思考 token 记在 `completion_tokens` 内：真实 smoke 的 `completion_tokens=34` 含 `completion_tokens_details.reasoning_tokens=31` | `scratch/original-model-smoke-1792370407818867378.json:13-20` |
| 2 | 决策请求 `max_tokens` 只有 512（transport 与 adapter 两处写死 512） | `autonomous_life_assembly.py:453,456,458` → `profile_model_transport.py:125` |
| 3 | 传输层只读 `choices[0].message.content`；空则抛 `PROVIDER_MALFORMED_RESPONSE("empty completion")`（改前） | `profile_model_transport.py:214-215`（改前） |
| 4 | 适配器把 `PROVIDER_MALFORMED_RESPONSE` 直接映射成 `ag1.FAILURE_CLASS_INVALID_JSON` | `agency_cognition_adapter.py:356`（改前） |
| 5 | 真实 `json.loads` 失败时原文被交给 AG-1，由 AG-1 报 `INVALID_JSON` | `agency_decision_ag1.py:3885-3891` |
| 6 | 首轮计数器形状与空content映射路径相容，但也不能排除真正坏JSON；没有首轮finish_reason/content长度，不能据此唯一归因 | `scratch/original-life-bounded-1792372735731635671.json` |

**反向验证**：把三个模块回退到 HEAD（`git show`，scp 到 scratch `prefix_old/`）再跑新测试，旧代码在
`finish_reason="length"` + `content=""` + `reasoning_content` 有完整 decision 的响应上，确实返回
`DECISION_ATTEMPT_FAILED:INVALID_JSON` —— 与生产观察到的词逐字相同。7 个新测试里 5 个在旧代码上
FAIL/ERROR（见 §4）。

## 3. 修复（最小、有证据、不放宽任何校验）

1. **上限给思考留位**：`MIN_DECISION_OUTPUT_TOKENS = 2048`
   （`agency_cognition_adapter.py:78`），`decision_output_cap()`（同文件 280 行）取
   `max(configured, request.max_output_tokens, 2048)` 作为**线上** `max_tokens`；`self.max_output_tokens`
   仍是 512（报告值不变）。这是上限不是花费：`reserve()` 每请求预留、`settle()`、usage 台账一字未改。
2. **诚实分类**：`agency_model_client.py:54-59` 新增 `PROVIDER_OUTPUT_TRUNCATED` /
   `PROVIDER_REASONING_ONLY` / `PROVIDER_EMPTY_CONTENT` / `PROVIDER_REQUEST_REJECTED`；
   `profile_model_transport.finish_send`（191 行起）按 `finish_reason` 与 `reasoning_content` 长度分流
   （261/263/264 行），非 auth/rate/5xx 的 4xx 判 `PROVIDER_REQUEST_REJECTED`（230 行）。
   适配器映射表 445-448 行给出各自 `failure_class`：`OUTPUT_TRUNCATED` / `REASONING_ONLY_NO_ANSWER` /
   `EMPTY_MODEL_OUTPUT` / `MODEL_REQUEST_REJECTED`。**`PROVIDER_MALFORMED_RESPONSE → INVALID_JSON` 原样
   保留**，真坏 JSON 依旧是 INVALID_JSON。
3. **推理永不变成 action**：`reasoning_content` 只用于 `reasoning_chars` 计数并被丢弃，永不进 `text`、
   永不解析、永不进 epoch 槽（`TransportResult` 59 行起只带形状字段）。
4. **严格 JSON 入口仍严格**：`unwrap_fenced_json()`（296 行）只在整个回复就是"一段 fence 且无其他字"
   时拆 fence（含 `\r\n`、无语言标签、单行 fence）；散文包着 fence 原样交给 AG-1，仍是 `INVALID_JSON`。
5. **结构诊断**：`cognition_result` 观测新增 `output_shape`（539 行）：
   `content_chars`/`fenced`/`json_parsed`/`parse_error_offset`/`finish_reason`/`reasoning_present`/
   `reasoning_chars`/`max_output_tokens_sent`；`base_record` 增加 `max_output_tokens_sent`。全部是长度/枚举/
   偏移，没有正文、没有提示词、没有私文。

AG-1 的严格枚举、候选 ID、理由码、hard constraint、ATOMIC 边界、候选爆炸校验**一行未改**；每请求预留
与"每 epoch 最多一次付费调用"未改。

## 4. 验证（只跑相关，不全回归、不 SHA、不 py_compile）

| 项 | 命令 | returncode | 结果 |
|---|---|---|---|
| 新精准回归 | `python3 tests/test_cognition_thinking_response.py`（WSL py3.12） | **0** | Ran 7 tests, OK |
| 反向对照（旧模块） | `prefix_negative_control.py`（把 HEAD 版本注入 `sys.modules` 再跑同一测试） | **1** | failures=3, errors=2；截断用例得到 `DECISION_ATTEMPT_FAILED:INVALID_JSON` |
| 受影响的既有套件 | `tests/cognition_reliability_suite.py`（同 `scripts/test.py` 的 PYTHONPATH） | **0** | checks 82/82 passed，`provider_calls_made=0`，所有 gate 为 0（DUPLICATE_PAID_CALL / LATE_RESULT_PROMOTION / UNKNOWN_SEND_STATE_AUTO_RETRY / MODEL_FAILURE_TO_NO_ACTION / canonical_activity_revision） |
| 结构诊断探针自检 | `rc1_thinking_shape_probe.py --self-test` | **0** | 3 个合成 body 的 shape 报告正确（无网络） |

新测试覆盖并**能捕获旧行为**的点：截断不再报 INVALID_JSON、只有 reasoning 时必须拒绝且不进 epoch 槽、
线上 `max_tokens` 必须 ≥ `MIN_DECISION_OUTPUT_TOKENS` 且仍请求 `response_format`、完整 fence 要拆、
散文 fence 不许猜、400 不许冒充"模型输出坏"、transport 只报形状不读 reasoning。

## 5. 给父级的下一步（一次有界复验，我不跑真实模型）

`scratch/rc1_thinking_shape_probe.py`（约 20s、默认 2 次调用、`--timeout 45`）：

```
python3 rc1_thinking_shape_probe.py --root /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094/one-day
```

它只打印：http status、`finish_reason`、`content` 长度、`reasoning` 长度、是否 fence、是否 JSON 对象、
parse error 字符偏移、usage 里的 token 计数、发出的 `max_tokens`；**不打印**提示词、回复正文、推理正文、
base_url、密钥。`--show-keys` 才额外给模型自己输出的顶层 key 名（永远不给值）。

读法：
- `legacy512` 变体（512 上限）若出现 `content_chars=0` + `finish_reason="length"` + `reasoning_tokens≈512`
  → 根因确认，改后的 `headroom` 变体应给出 `json_object=true`；
- `headroom` 变体若 4xx 且 `error_type` 指向 `response_format`/`max_tokens` 不支持 → 是网关请求被拒
  （现在会明确报 `MODEL_REQUEST_REJECTED`），那需要父级决定是否去掉 `request_json_object`，不能靠放宽
  JSON 入口变绿；
- `headroom` 变体若 `json_object=false` 且有 `parse_error_offset` → 是真实输出不合法，按原样交给 AG-1 拒绝。

## 6. 剩余限制

- 我**没有**跑真实模型与原实例（按分工），"2048 够不够"只能由父级这次复验确认；若仍截断，`MIN_DECISION_OUTPUT_TOKENS`
  是唯一需要上调的常量。
- 延迟风险：思考上限抬高后单次调用可能变长；`timeout_s` 仍由装配层给（resident 默认 30s），
  超过仍是 `LATE_RESULT_DISCARDED`（记录并丢弃，不会变成决策），我没有放宽这个语义。
- `reading_response` / `artifact_edit` 两条路径同样写着 `max_output_tokens=512`（`autonomous_life_assembly.py:385,422`），
  它们不是本次观察到的失败路径，我**没有**改；若父级后续发现它们在 thinking 模型上同样截断，可复用同一常量。
- `agency_gateway_transport.py`（`ctx.llm` 宿主传输）不在本实例路径上，未改。

## 父级结构复验补证

`scratch/rc1-thinking-shape-1792373842086106530.json`：两次HTTP200、合成候选、无owner动作。2048上限stop/content330chars/reasoning2433chars，completion700含reasoning610，JSON对象true；512上限length/content0/reasoning2061chars，completion512全为reasoning512，JSON对象false。诊断不经过原实例Life请求账本，必须与Life有效决策分开记。原实例修后gen2 tick无新增请求（累计计数仍1），不是有效决策复验。
