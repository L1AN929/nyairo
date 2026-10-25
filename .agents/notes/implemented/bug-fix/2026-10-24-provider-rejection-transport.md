# Agent Note: provider 4xx 拒绝必须把已读到的 HTTP 状态带到错误对象

Status: implemented

## Problem

2026-10-24 的 fastday-f 结束后，`resident_journal` 有 12 次
`cognition_send_started` / `cognition_send_failed` / `cognition_provider_error`，
`error_category=PROVIDER_REQUEST_REJECTED`、`response_received=true`，
但 `response_status` 12 次全为空。已提交 Decision 0、采纳 0、动作 0。

按 `profile_model_transport.finish_send` 的既有映射，`PROVIDER_REQUEST_REJECTED`
只在 `status >= 400` 且不是 401/403/429/5xx 时产生。也就是说：12 次都是
**4xx 请求被拒**，但到底是 400、404、405 还是 422，事后无法回看，因为
`finish_send` 虽然已经读到了 `response.status`，构造 `AgencyModelClientError` 时
没有把它传进去；`agency_cognition_adapter` 只能从 `exc.status` 取到 `None`。
[2026-10-22-live-model-diagnostics-binding.md](2026-10-22-live-model-diagnostics-binding.md)
写明 journal 应记 `response_status(若有)`，这里实现与决定不一致。

同一实例的配置里 `NYA02_MODEL_BASE_URL` 以大写路径段 `/V1` 结尾，
`finish_send`/`_target` 不做大小写归一，最终路径恰为 `/V1/chat/completions`，
不重复 `/v1`、不改写为 `/responses`；请求体仍是既有 chat-completions 形状，
没有 provider 特判。外部网关是否接受该大小写、4xx 是否来自路径或参数，
离线无法证明，本决定不改用户给出的 `/V1`、不换模型名、不猜凭证问题。

## Decision

- `profile_model_transport.finish_send` 在四个基于 HTTP 状态的失败分支
  （401/403、429、5xx、其余 4xx）抛出 `AgencyModelClientError` 时附加
  `status=status`。只带数字状态；响应正文仍只存在于异常文本里，adapter 不落盘。
- adapter、安全 sink、journal 格式都不改：`response_status` 本来就是
  `0..599` 的有界整数白名单字段，缺失时依旧省略，不补 0。
- 连接失败/超时（没有响应）仍不设置状态，保持“没有响应”与“响应为某个 4xx”
  可区分。
- 新增 `tests/test_f_provider_rejection.py`（本机 loopback，零外部请求）钉住：
  `/V1/chat/completions` 原样、无重复分隔符、请求体字段不变；400/404/422 →
  `PROVIDER_REQUEST_REJECTED`，401/403/429/5xx 的分类不变且 `exc.status` 是数字；
  经 `assembly.model_cognition` 后 observer 记录带数字 `response_status`；
  连接失败仍无状态。
- 同步修正 `tests/test_v04_day_model_diagnostics.py` 的 503 用例：原断言
  `assertNotIn("response_status", failed)` 钉的是这次要修的丢失行为，改为
  `assertEqual(failed["response_status"], 503)`；超时用例仍断言无状态。
- 不新增重试、不改预算/账本/cap/时钟/gate，不改 provider 协议或依赖。

## Alternatives considered

- **只在 `PROVIDER_REQUEST_REJECTED` 分支补状态**：立论是改动面最小，且正好覆盖
  f 的失败类，既有 503 测试一行不动。否决：同一个函数里 5 个分支共享同一份状态，
  只让其中一个能回看会让下一次 401/429/503 继续丢状态，修的是症状不是接缝。
- **把响应正文或错误 message 也落 journal**：立论是能直接看到网关说了什么。否决：
  响应正文可能夹带任意内容，既有安全 sink 明确逐值白名单重建；只记录可枚举的
  数字状态和预定义错误类，才能在不新增敏感面的前提下定位问题。
- **把 base_url 的 `/V1` 自动转小写再请求**：立论是 OpenAI 兼容端点通常写作 `/v1`。
  否决：用户明确给了 `/V1`，网关实际路由大小写未验证；擅自归一可能把一次 4xx
  换成另一种 4xx，却让人误以为问题已修。先把真实状态记下来，再决定是否改配置。
- **新增真实 probe 或健康检查来拿状态**：否决：本单 0 产品、不得请求外部网关，
  且 probe 本身会消耗额度并绕过账本。

## Consequences

- **收益**：下一次真实 4xx 会在 resident journal 留下数字 `response_status`，
  400/404/未被支持的路由/参数拒绝可以区分；不需要新字段、新存储或新协议。
- **代价**：journal 的失败行会多一个既有白名单字段；旧实例历史记录不倒写。
- **上限**：本单没有调用真实 provider，也没有启动实例，因此
  **f 的 4xx 具体状态与根因仍是 UNKNOWN**。测试只证明 URL 构造形状和错误分类
  接缝，不证明网关接受 `/V1`、不证明模型/参数可用。共同预算仍为 55/300（f 的
  12 次失败不退），没有新增产品请求。
