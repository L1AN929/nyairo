# Agent Note: 真实认知输出截断 —— 失败形状缺失与有界输出余量调整

Status: implemented

## Problem

2026-10-22 20:51–20:55 同一实例的一段真实日常里，3 次认知请求中 2 次以
`DECISION_ATTEMPT_FAILED:OUTPUT_TRUNCATED` 失败（`response_received=true`）。成功那次
`cognition_result` 记录了 `max_output_tokens_sent=2048`、`reasoning_chars=7760`、
`content_chars=199`、`finish_reason=stop`；两次失败只有
`error_category=PROVIDER_OUTPUT_TRUNCATED`，没有 finish_reason、字符数和 usage 计数，
因此**无法回答失败时 cap 是否真的被思考吃满**。失败响应原文没有被保留，本轮也不得重发，
所以不能拿成功那次的 2048 反推失败请求的实际 token 量。

代码链路上有两件事同时成立：

1. **截断判定已有，但事实只进了异常消息。** `profile_model_transport.finish_send`
   在 `finish_reason=="length"` 且 `content` 为空时抛
   `AgencyModelClientError(PROVIDER_OUTPUT_TRUNCATED, "<shape 文本>")`；字符数、推理长度、
   usage 都只拼在 message 里。`AgencyModelClientError` 本身没有结构化元数据字段
   （`agency_model_client.py:65`），adapter 的失败分支
   （`agency_cognition_adapter.py:1297` 起）因此只能记录类别和 `response_received`。
2. **resident 的安全 sink 丢掉了顶层 cap。** adapter 的 `base_record` 把
   `max_output_tokens_sent` 放在记录顶层，而 `safe_cognition_diagnostic`
   （`autonomous_life_resident.py:202`）只从 `output_shape` 里取这个字段。成功记录的
   `output_shape` 有它，失败记录没有，于是现场恰好缺少数一数二的关键事实。

另外，`ProductionAgencyCognitionAdapter.decision_output_cap()` 的实际线上 cap 是
`max(configured=512, request.asked, MIN_DECISION_OUTPUT_TOKENS=2048)`，即所有思考型决策
请求都被送到 2048。旧决定见
[2026-10-19-rc1-thinking-model-decision-invalid-json.md](2026-10-19-rc1-thinking-model-decision-invalid-json.md)：
当时把 512 抬到 2048，理由是思考 token 与答案共用同一个 cap。成功那次只能说明推理内容
较长：7760 是字符数不是 token 量，199 是答案正文字符数也不是剩余 token 预算；没有失败请求
的 usage，**无法证明 2048 在失败时已被耗满**。「思考与答案共用 cap」只是与现象一致的机制，
属于待验证解释，不构成本次失败根因的证明。

## Decision

1. **`length` 一律按截断拒绝。** `finish_send` 只要看到 `finish_reason=="length"` 就抛
   `PROVIDER_OUTPUT_TRUNCATED`，不再区分正文为空、部分 JSON 或恰好完整的 JSON。截断后的
   完整 JSON 也不进入 epoch 槽；不拼接、不补全、不放宽 AG-1 校验器。
2. **截断/推理独占/空回复携带安全元数据。** 新增 `_shape_metadata()`，只装
   `finish_reason`、`content_chars`、`reasoning_chars`，以及 usage 中**存在且为非负整数**
   的 `prompt_tokens`/`completion_tokens`/`total_tokens`/`reasoning_tokens`。缺失或类型不合法
   的计数直接省略，绝不写 0。`AgencyModelClientError` 新增 `metadata` 字段承载这份映射。
3. **adapter 失败分支把形状写进 `cognition_send_failed`。** `failure_output_shape()`
   二次白名单重建（finish_reason 只接受封闭词表，计数只接受有界非负整数），并补上本次
   实际送出的 `max_output_tokens_sent`。仍然是既有的 `_observe` 与 required 语义：send
   marker 写不进 journal 就不发请求。
4. **resident 安全 sink 接受四个 token 计数。** `safe_cognition_diagnostic` 在
   `output_shape` 的既有白名单里增加 `input_tokens`/`output_tokens`/`total_tokens`/
   `reasoning_tokens`，全部走 `_bounded_int`；未知字段、任意 key、正文、推理文本、
   异常 message 依旧不落盘。
5. **输出 cap 单一有界抬高：2048 -> 4096。** 只改
   `MIN_DECISION_OUTPUT_TOKENS`，`decision_output_cap()` 仍取
   `max(配置值, 请求值, 下限)`，用户显式更高配置（例如 8192）照原样保留。这是**已实施的
   预防性余量调整**：既不是对真实截断根因的证明，也不宣称离线或真实链上已验证 4096 能
   消除真实失败；不按失败次数继续倍增，也不新增 `reasoning_effort`/thinking 参数。
6. **不动的东西。** 每请求预留/settle/失败不退款、deadline、late 丢弃、单次付费调用、
   不自动重试、`reasoning_content` 永不解析为决定、AG-1 schema 全部保持原样。

## Alternatives considered

- **只改错误名称或把 length 吞成静默失败**：立论是改动最小。否决：这正是旧 bug 的形状——
  把截断说成别的类别，现场仍然无法区分；吞掉 length 还会让部分 JSON 继续流向校验器，
  把「被截断」误报成「模型写了坏 JSON」。
- **从 `reasoning_content` 里解析决定，或把截断的 JSON 拼接补全**：立论是模型其实已经想清楚，
  内容就在附近，救回来最省一次调用。否决：推理是推导不是承诺；拼接等于替模型编造答案。
  两者都破坏 §14 的严格入口，即使截断后的正文恰好是完整 JSON 也不改变这一点。
- **保持 2048，只补诊断**：立论最强——本轮没有失败请求的 usage，无法证明 2048 就是根因，
  只加可观测性最诚实。否决理由是：诊断补上之后下一次仍要面对同一条有界 cap，而成功样本的
  推理内容较长（7760 字符，非 token 量；199 字符答案也不是剩余预算），值得预留余量；但
  **4096 只是预防性选择，既非离线证实，也没有真实链证据证明它能解决真实失败**。抬到 4096
  是单步、有界、可回退的余量调整，成本上限明确（见 Consequences），不改写真实 provider 事实。
- **按失败次数自动倍增，或按模型名猜 provider 能力**：立论是自适应的最快。否决：无界增长会
  把预算上限变成摆设，按模型名猜能力则是把未知当成已知；下一次若仍截断，应先取 provider
  侧事实，而不是继续加 token。
- **新增诊断 sink/第二账本**：立论是隔离彻底。否决：旧笔记已淘汰该方案；resident 的 journal
  就是这次失败唯一需要并排阅读的记录，扩既有 `output_shape` 白名单代价最小。

## Consequences

- **收益**：下一次失败的 `cognition_send_failed` 会带
  `finish_reason`、`content_chars`、`reasoning_chars`、`max_output_tokens_sent` 和 provider
  实际报的整数 token 计数；截断、只有推理、空回复、真正坏 JSON 四类不再互串。负控证明
  恶意 usage/finish_reason/字段值不会进入 journal，缺失计数不会被伪造成 0。
- **代价**：单次决策请求的 `max_tokens` 天花板从 2048 抬到 4096；若模型真的写满，单请求
  最大 completion token 翻倍。实际花费仍由模型停止决定，预留/settle 与请求账本按次数计，
  本次没有改动；`length` 现在对部分/完整 JSON 都拒绝，可能让极少数「恰好写满 cap 且内容
  完整」的响应从被接受变为失败——这是有意的保守选择。
- **已知上限**：本轮没有调用真实 provider（硬约束），因此**不能宣称真实截断已消失**；
  失败请求当时的具体 token 量已不可恢复，只能等下一次真实运行产生带形状的失败记录。
  4096 是否被网关接受、是否足够，同样未在真实链上验证。
