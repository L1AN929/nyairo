# Agent Note: 阅读响应真正发送的输出上限继承 client 配置

Status: implemented

## Problem

正式装配 `autonomous_life_assembly._model_reading_response` 建的是
`AgencyModelClient(..., purpose="reading_response", max_output_tokens=2048)`，再包一层
`BudgetedModelClient`。但 `ModelReadingResponse(client)` 的旧默认 `max_output_tokens=512`
会在 `respond()` 里作为显式实参传给 `complete`，在**发送边界**把构造时的 2048 覆盖成 512。
Live C 的结构化记录里 `reading_response` 的实际 cap 就是 512，`finish_reason=length`；
两次失败后停，累计请求到 568。也就是说，装配处写的 2048 从来没真正发出去过。

## Decision

1. `ModelReadingResponse.__init__` 的 `max_output_tokens` 默认改为 `None`：没有显式值时
   **继承底层 client 自己配置的上限**（正式链经 `BudgetedModelClient.__getattr__` 透明读到
   `AgencyModelClient.max_output_tokens=2048`）；显式值继续优先，包括显式给 512 或更小。
2. 没有 `max_output_tokens` 属性的旧替身沿用 512 回落（`DEFAULT_MAX_OUTPUT_TOKENS`）；
   bool / 非整数（显式传入或 client 声明）一律 `MAX_OUTPUT_TOKENS_INVALID`，不猜成有效上限。
3. `BudgetedModelClient` 未改：它本来就用 `__getattr__` 透传，没有挡住属性，加显式 property
   只会多一份要同步的副本。
4. 新增 `tests/test_reading_response_token_cap.py`：经真实 assembly 构造、真实
   `AgencyModelClient` + `BudgetedModelClient`，只有最后一段 transport 被替身化，证明真正
   发到 transport 的是 2048；显式 512 优先也到发送边界。D 的 audit 增加只读的
   `reading_cap`（构造期观测，`measured_by_send: false`，缺失 UNKNOWN），不拿目标值冒实发。

## Alternatives considered

- **只改 assembly，在 `ModelReadingResponse(client, max_output_tokens=2048)` 显式传。**
  最强立论：一处改动、不动类默认。否决原因：本批次 assembly 是只读锚点；而且缺陷在类的
  「无显式值时用旧默认覆盖 client 配置」这条通用路径上，只改一个调用点会让其它调用者继续踩。
- **把类默认直接改成 2048。** 最强立论：最少的字。否决原因：会把一个值硬编码进类，client
  若配置了别的上限仍被忽略；继承才是「读装配的可信属性」。
- **给 `BudgetedModelClient` 加显式 `max_output_tokens` property。** 最强立论：属性来源更显式。
  否决原因：`__getattr__` 已经透明透传，属性并未被挡住；新增副本只为同一事实多一处维护点。
- **对 `finish_reason=length` 自动重试或截断。** 最强立论：立刻不再失败。否决原因：截断等于
  改写她写的内容；重试会额外消耗预算且仍可能截断。本单只修 cap 传递，不承诺永不截断。

## Consequences

- 正式阅读请求现在真的带 2048 上界；显式 override（含更小的 512）仍优先并到达发送边界。
- 旧替身（无该属性）保持 512；bool/非法字符串不会被静默当成有效 cap。
- 生产模型 client 的全局默认、其它 writer、预算 reserve/settle/失败记账与重试语义均未改。
- 既有内容失败负控（显式 512 对构造 2048）改为显式传 512，保留原意。
- 未保证：2048 仍可能被推理过程或长输出吃满；本单不声称所有截断消失。
- 相邻既有决定：[阅读响应的有限内容协议](../feature/2026-10-25-v04-reading-response-annotation-plan.md)、
  [内容失败的安全 stage/cap/finish_reason](./2026-11-06-content-failure-metadata.md)。
