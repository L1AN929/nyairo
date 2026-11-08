# Agent Note: 内容失败事实留在既有异常 metadata 通道 —— 持久 marker 不扩 schema（CONTRACT_STOP）

Status: implemented

> 后续接管（2026-11-06 同日）：用户明确批准有限扩展后，`bounded_failstop.json` 已增加
> `stage / actual_cap / finish_reason` 三个定型顶层键，持久部分由
> [marker 扩展笔记](2026-11-06-content-failure-marker-extension.md) 接管。下面的 Problem /
> Decision / Alternatives / Consequences 保留当时“未获授权、停止持久透传”的原始判断与
> 事实，不表示这些字段至今仍不落盘；安全事实仍只走本笔记定义的白名单。

## Problem

2026-11-06 组合 A 的唯一真实批在 decision（`finish_reason=stop`、`max_output_tokens_sent=4096`）
成功之后，content 预留失败并以 `OUTPUT_TRUNCATED` 锁批。安全提取只能证明 `CONTENT_FAMILY`：
失败的是 decision 之外的 content 请求；具体是哪个写入器（artifact_edit / reading_response /
shared_practice / local_reply）、本次实际送出的 cap、异常里本来就有的 finish_reason 全都未知，
父级结果只能写 `UNKNOWN`。

[2026-10-22 的输出截断笔记](2026-10-22-cognition-output-truncation.md) 已经把 finish_reason、
字符数和整数 token 计数放进 **`AgencyModelClientError.metadata`**，并让认知 adapter 的
`cognition_send_failed` 记录 `output_shape`。[诊断绑定笔记](2026-10-22-live-model-diagnostics-binding.md)
说明 content 写入器不经过认知 adapter 的 observer。content 失败之后只剩两个既有通道：

- `AgencyModelClientError.metadata`：异常对象上的内存通道，10-22 已定义、已按白名单声明
  “只放 shape 事实”，不落盘。
- `bounded_failstop.json`：`LifeRequestBudget.note_failure` 同步落盘的不可逆 marker，持久合同
  只有 `run_id / reason_code / failure_category / at` 四个顶层键，**没有任何 metadata 容器**。

上一版实现把 `stage / actual_cap / finish_reason` 作为三个顶层键写进这个 marker，并把
“complete 没传 cap 时退到 client 构造值”当成实际 cap。前者是未经授权扩持久 schema，后者是把
从未送出过的构造默认冒充实发值；两项都不成立，已撤回。

## Decision

1. **持久 marker 不扩 schema（CONTRACT_STOP）。** `note_failure(failure)` 恢复为原来的四键
   payload，删除 `metadata=` 参数与三个诊断顶层键。`bounded_failstop.json` 没有容纳安全事实的
   既有容器；按原任务“无法在现合同做则 STOP，不自行扩 schema”，**停止持久透传**，不新增
   第二 sink、文件、日志 kind、表或 owner 协议。
2. **安全事实只重建在既有 `AgencyModelClientError.metadata` 通道。** 新增具名函数
   `attach_safe_failure_metadata()`，在 `BudgetedModelClient.complete` 的 failstop 分支捕获异常后
   原地重建该 dict：只保留白名单整数计数（`content_chars`、`reasoning_chars` 等）与三个派生事实，
   异常 message、purpose 字符串和任意自由文本键一律丢弃。默认 `failstop=False` 路径不调用它，
   异常 metadata 与旧路径逐字一致。
3. **cap 只认本次实发。** `_actual_cap()` 只取本次 `complete` 的 `max_output_tokens` 实参，
   **不再退到 `inner.max_output_tokens` 构造值**；`safe_failure_metadata()` 先取 transport 异常
   metadata 的 `max_output_tokens_sent`（真实发出的值），没有再取本次实参，两者都缺即 `UNKNOWN`。
   数值必须严格是 `int`（`bool` 不算），范围 `0..10_000_000`。
4. **stage / finish_reason 只走闭集。** stage 取实际 client 构造的固定 purpose（或包装器显式传入
   的 `writer_purpose`），只接受 `artifact_edit / reading_response / shared_practice / local_reply`；
   finish_reason 只接受 `stop / length / content_filter / tool_calls / function_call`。缺失、自由文本
   或其它值一律 `UNKNOWN`，绝不解析异常 message。
5. **行为不变。** 失败仍在返回前同步落不可逆 marker，下一次 reserve/transport 被挡；零重试、
   不退款、不因诊断失败放过发送。选择、预算、重试、TTL、NO_ACTION 均未改。

## Alternatives considered

- **在 `bounded_failstop.json` 上新增三个顶层键（上一版做法）**：立论是复用既有文件、读取方只取
  `failure_category`，看起来“没有新增 sink”。否决：复用同一个文件不等于没有扩 schema；四键就是
  当前持久合同，加键会让严格的读取方与历史 marker 分叉，且原任务明确禁止扩 schema。
- **给 marker 加一个嵌套 `metadata` 容器再放三键**：立论是键集合不变、语义更整齐。否决：那仍是
  新的持久结构，等于换一种方式扩 schema；合同里没有这个容器，不能由施工单自行发明。
- **把安全事实塞进 resident journal 的新 kind**：立论是记录面最完整，和认知诊断并排可读。否决：
  这正是既有笔记淘汰过的第二写面；content 写入器不经过认知 adapter，接进去要改 journal 读取方，
  超出本缺口。
- **cap 缺实参时退到 client 构造值**：立论是字段现成、能给出一个确定数字。否决：真实阅读 writer
  用 512 调、client 构造是 2048；构造值可能从未送出，记录它比 `UNKNOWN` 更坏。
- **用异常 message 或成功请求反推 stage / cap**：立论是不用改 client 构造。否决：message 可能
  夹带私文且不是闭集；decision 成功那次只证明它自己的 4096，不能证明 content 的实参。

## Consequences

- **收益**：下一次真实 content 失败时，抛出的 `AgencyModelClientError.metadata` 会带固定 writer、
  本次实发 cap 和 provider 的 finish_reason；这些值都能在调用栈内直接读到，且不会把自由文本
  带进去。既有 marker 保持原四键，不破坏任何历史读取方。
- **代价**：这些事实**不落盘**。marker 里仍然只有 `failure_category`；进程退出后拿不到 stage/cap/
  finish_reason，必须在失败现场读取异常对象。基于 `failure_class` 返回值（没有异常对象）的失败
  也没有可挂载的通道，本单未补。
- **已知上限**：旧 A 那次失败的具体 writer、实际 cap 与 finish_reason 永久未知（未重跑产品，
  累计 524/1000 不变）。本改动不提高任何 token 上限，也不证明下次不会截断。
- 互链：[输出截断与 4096 余量](2026-10-22-cognition-output-truncation.md)、
  [诊断绑定](2026-10-22-live-model-diagnostics-binding.md)、
  [idle2 有界失败锁](../testing/2026-11-06-idle2-bounded-observation.md)、
  [持久三键扩展（接管持久部分）](2026-11-06-content-failure-marker-extension.md)。
