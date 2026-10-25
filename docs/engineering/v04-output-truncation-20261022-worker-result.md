# v0.4 认知输出截断：定向诊断与离线修复 · worker 交付

日期：2026-10-22。唯一 writer：本工单 worker。结论：失败路径的诊断缺口与单一 cap 诱因已离线修复并验证；**未调用真实产品模型，不能宣称真实截断已消失**。失败请求当时的具体 token 量无法恢复。

## 一、根因与证据边界

### 已证事实（来自 journal 与代码）

- 第二段真实运行 3 次认知请求，2 次失败为 `PROVIDER_OUTPUT_TRUNCATED`、`response_received=true`；成功那次 `max_output_tokens_sent=2048`、`reasoning_chars=7760`、`content_chars=199`、`finish_reason=stop`。
- `PROVIDER_OUTPUT_TRUNCATED` 在离线代码里只有一个产生点：`profile_model_transport.OpenAICompatibleTransport.finish_send`（改前 255-261 行）在 `content` 为空且 `finish_reason=="length"` 时抛出。因此「供应方返回 length、正文为空，被判为截断」由 journal 与代码共同证明。
- 失败 journal 缺 finish_reason、字符数、usage 计数；adapter 的 `cognition_send_failed` 只带类别与 `response_received`。
- `safe_cognition_diagnostic` 只从 `output_shape` 读取 `max_output_tokens_sent`，adapter 的 `base_record` 却把它放在顶层，失败行因此连实际 cap 都没有留下。
- `ProductionAgencyCognitionAdapter.decision_output_cap()` = `max(配置 512, 请求值, MIN_DECISION_OUTPUT_TOKENS=2048)`，所有思考型决策请求实际都发 2048；`autonomous_life_assembly.model_cognition` 只传 512，没有更高显式配置。

### 未知边界

- **本次失败请求的实际 token 量未知**：失败响应未被保留，不能拿邻次成功的 2048 反推，也不得重发。
- 「思考吃满 2048」是与证据一致的机制，不是对根因的证明；4096 是预防性余量，不是已证实足够的修复。
- 未连接真实 provider，4096 是否被网关接受、是否消除截断均未验证。

## 二、改动文件与具名函数

| 文件 | 具名改动 |
|---|---|
| `components/life/scripts/profile_model_transport.py` | 新增 `_shape_metadata()`；`finish_send` 对任意 `finish_reason=="length"` 抛 `PROVIDER_OUTPUT_TRUNCATED`（空正文/部分 JSON/完整 JSON 一致），错误携带 finish_reason、字符数与非负整数 token 计数；`reasoning_content` 仍只计长度并丢弃 |
| `components/life/scripts/agency_model_client.py` | `AgencyModelClientError.__init__` 新增 `metadata` 字段（默认空映射） |
| `components/life/scripts/agency_cognition_adapter.py` | `MIN_DECISION_OUTPUT_TOKENS` 2048 -> 4096；新增 `failure_output_shape()` 白名单重建；`cognition_send_failed` 增加 `output_shape`（含实际 `max_output_tokens_sent`） |
| `components/life/scripts/autonomous_life_resident.py` | `safe_cognition_diagnostic` 的 `output_shape` 白名单增加四个 token 计数（有界整数）；既有 dirty 未覆盖 |
| `tests/test_output_truncation_diagnostics.py` | 新增 13 例离线测试（loopback） |
| `.agents/notes/implemented/bug-fix/2026-10-22-cognition-output-truncation.md` | 新决策笔记；与 10-19、10-22 两篇互链 |
| `.agents/notes/implemented/bug-fix/2026-10-19-…md`、`2026-10-22-live-model-diagnostics-binding.md` | 仅追加互链与仍成立事实，旧理由未改写 |

未改：AG-1 校验器/schema、assembly、budget/ledger、deadline/late 语义、重试策略、provider/SDK、实例与 root、配置与密钥。

## 三、实际命令、退出码与结果

均在仓库根 WSL 执行：

1. `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_cognition_thinking_response tests.test_v04_day_model_diagnostics tests.test_output_truncation_diagnostics tests.test_decision_schema_refusal_diagnostics tests.test_one_day_life_w05r.RequestBudgetTest`
   - 改前基线（不含新模块）：50 例 OK，exit0。
   - 改后：**63 例 OK，exit0**（新增 13 例）。
2. 写 note 前检索：`grep -rln "OUTPUT_TRUNCATED|输出截断|max_output_tokens|2048" .agents/notes/` 命中 10-19、10-22 两篇，均已互链；无完全取代，按「部分取代」处理。
3. notes 三脚本、`git diff --check` 见下方验收记录。

失败历史：新模块首轮 `test_a_truncated_answer_arriving_after_the_deadline_is_not_promoted` 因 socket 超时先于 adapter deadline 触发，分类为 `MODEL_TIMEOUT`；改为 adapter 级确定性替身（客户端延迟后抛截断错误）后通过。该失败保留，不掩饰。

## 四、修复行为与安全负控

- **截断三态不串**：`length`（空/部分/完整 JSON）→ `OUTPUT_TRUNCATED`；`stop` + 仅 reasoning → `REASONING_ONLY_NO_ANSWER`；`stop` + 真正坏 JSON → 原样交给 AG-1，不因截断分类被改写。
- **不把 length 当成功**：完整 JSON + `length` 仍拒绝，`distinct_epochs_paid=0`，不进 epoch 槽。
- **cap 可断言、不加调用**：配置 512 时 payload `max_tokens=4096`，`hits=1`；显式配置 8192 时原样发送 8192。
- **失败形状持久化**：resident journal 的 `cognition_send_failed` 实测含 `finish_reason=length`、`content_chars=0`、`reasoning_chars`、`max_output_tokens_sent=4096`、`input/output/total/reasoning_tokens`。
- **私文负控**：恶意 usage 值、非法 finish_reason、任意私有 key、reasoning 中的私文标记均未进入 journal；非负整数之外的计数被省略，不补 0。
- **兼容与预算**：无 observer 旧用法仍只发一次并抛同类错误；截断失败在持久账本上恰好消耗 1 次 `utc_day`/`run_window`，无 release API；adapter `late_results_promoted=0`。
- **required 语义未变**：既有「send marker 写失败则零请求」测试继续通过（`test_v04_day_model_diagnostics`）。

## 五、未验证项

- 未连接真实 provider，未重发失败请求；真实截断是否消失、4096 是否被网关接受、下一次真实请求的实际 usage 均未知。
- 未跑全库套件、未做浏览器点验、未启动/修改任何实例。
- 新 cap 的代价：单次决策请求 completion token 天花板 2048 -> 4096，若模型写满则单请求最大 token 翻倍；实际花费仍由模型停止决定，预留/settle 与按次账本未变。
