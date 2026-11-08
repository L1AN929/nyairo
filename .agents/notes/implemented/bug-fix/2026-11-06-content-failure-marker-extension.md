# Agent Note: bounded_failstop 有限持久扩展 —— 三个定型诊断键

Status: implemented

## Problem

2026-11-06 组合 A 的 content 请求以 `OUTPUT_TRUNCATED` 锁批之后，`bounded_failstop.json`
只留下 `run_id / reason_code / failure_category / at` 四个键。失败的是哪个固定写入器
（artifact_edit / reading_response / shared_practice / local_reply）、本次实际送出的 cap、
provider 给的 finish_reason，进程退出后都无法从盘上读到。

[同日的前一篇决定](2026-11-06-content-failure-metadata.md) 把这三项安全事实只重建在
`AgencyModelClientError.metadata` 内存通道，并以“持久合同只有四键、施工单不得自行扩 schema”
为由停止持久透传（CONTRACT_STOP）。该判断在当时成立：批准范围里没有扩 schema 的授权。
现在用户明确回复“允许有限扩展”，授权范围恰好是给现有 `bounded_failstop.json` 增加
`stage / actual_cap / finish_reason` 三个定型顶层字段，不新增文件、不改 token 上限或重试。
本笔记接管前一篇的**持久部分**；前一篇的其余结论（安全事实只走白名单、cap 不退回构造值）
继续有效。

## Decision

1. **只加三个顶层键。** `bounded_failstop.json` 的键集合由原四键加
   `stage / actual_cap / finish_reason` 构成，不再有别的顶层字段。原四键
   `run_id / reason_code / failure_category / at` 原样保留。
2. **仅在内容失败异常路径透传。** `note_failure(failure, *, diagnostics=None)` 新增可选
   诊断参数；只有 `BudgetedModelClient.complete` 捕获异常、调用
   `attach_safe_failure_metadata()` 之后把返回的三项传进去。`safe_failure_diagnostics()`
   再把传入值按同一套闭集/整数规则重建一次，调用方无法靠传自由文本扩宽字段。
3. **字段规则。** stage 只认实际 client 构造的固定 purpose 或包装器显式传入的
   `writer_purpose`，闭集为 `artifact_edit / reading_response / shared_practice /
   local_reply`；actual_cap 优先本次异常 metadata 的 `max_output_tokens_sent`，其次本次
   `complete` 的 `max_output_tokens` 实参，两者都缺即 `UNKNOWN`，严格 `int`（排除 bool），
   范围 `0..10_000_000`；finish_reason 只认异常 metadata 的闭集值
   `stop / length / content_filter / tool_calls / function_call`。缺失或其它值一律
   `UNKNOWN`，不解析异常 message，不由成功请求反推，不退到构造默认值。
4. **只送这三个事实。** usage/token 计数、正文、异常 message、endpoint、env、headers
   以及其它 metadata 键都不进入 marker。原有非模型 marker（deadline、预算持久化失败、
   schema retry 等）不传 diagnostics，保持四键原形状。
5. **兼容旧 marker。** 旧的四键 marker 仍被 `check_failstop` 识别，并在 reserve/send
   之前阻断；不做补写、迁移或重算。`failstop=False` 的默认路径不建 marker、不改异常；
   只有 `failure_class` 返回值而无异常对象的失败没有可信字段，保持四键。
6. **行为不变。** 失败仍在返回前同步落不可逆 marker，诊断缺字段或异常不能让锁失效；
   下一次 reserve/transport 依旧被挡。预算、类别、超时、TTL、选择、NO_ACTION、不退款、
   不新重试全部保持原样。

## Alternatives considered

- **保持只落内存（前一篇的结论）**：最强理由是零 schema 变化、不触碰任何历史读取方，且
  `AgencyModelClientError.metadata` 现场就能读到。否决原因：进程退出后事实消失，正是这次
  调查需要的 stage/cap/finish 无法在盘上留下；用户已明确批准三个顶层键的有限持久化。
- **嵌套一个 `metadata` 容器再放三键**：最强理由是顶层键语义更整齐、将来还能扩展。否决：
  批准的是三个指定顶层字段，嵌套容器等于另一种 schema，且会改变读取方的取值路径。
- **新增第二 sink、日志 kind 或表**：最强理由是隔离彻底、不碰既有 marker。否决：同样一份
  失败事实写两处，读取方要同步维护；既有笔记已淘汰该路线，本次授权也没包含新 sink。
- **把异常 metadata 整体落盘**：最强理由是信息最全。否决：会带入 usage、正文和自由文本，
  正是前一篇反复要求排除的内容。
- **cap 缺实参时退回 client 构造值**：最强理由是字段现成、总能给出数字。否决：真实阅读
  writer 用 512 调、client 构造是 2048，构造值可能从未送出，比 `UNKNOWN` 更坏。

## Consequences

- **收益**：下一次真实 content 失败时，`bounded_failstop.json` 会同时带固定 writer、本次
  实发 cap 和 provider 的 finish_reason；异常 metadata 与盘上 marker 同源，进程退出后仍可
  诊断。旧四键 marker 继续有效，历史读取方只需容忍新增三键。
- **代价**：marker 的持久 schema 从四键变为七键；严格拒绝未知键的读取方需要同步放宽。
  旧 marker 不补写，历史 A 的 writer/cap/finish 仍然永久未知。
- **验证边界**：只跑离线四聚合 case，正式 client/预算包装器与脚本 transport，外加真实阅读
  writer 的负控；覆盖三字段与异常事实一致、构造 2048/未传 cap/无实发值记 `UNKNOWN`、
  实发 777 优先实参 512、bool 不认、自由文本不进 marker、精确七键、默认关闭兼容、
  旧四键 marker 阻断且不被改写、非模型 deadline 保持四键。未发真实请求，不证明截断消失，
  累计请求额度仍为 524/1000。
- 互链：[安全事实与 CONTRACT_STOP 历史](2026-11-06-content-failure-metadata.md)、
  [输出截断与 4096 余量](2026-10-22-cognition-output-truncation.md)、
  [有界失败与 stop marker](../testing/2026-11-06-idle2-bounded-observation.md)。
