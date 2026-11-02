# 相机慢响应：客户端预算跟服务端对齐，断连安静结束，重复标签保序去重

Status: implemented

## Problem

客户端所有动作共用 10 秒读超时，而相机在手机侧合法预算远超（转换 90s + 512px 生成 300s）：客户端先判 PHONE_UNREACHABLE 挂断，手机仍生成并保存，回写时 BrokenPipe 又被 `_dispatch` 通用异常当成内部错误再写第二个 500；同一次真实输出里转换器把同一个英文标签重复了 9 次。

## Decision

`phone_tool` 只为 `camera` 传 480 秒预算（覆盖 470 秒最坏值），其它动作仍 10 秒且不加自动重试，超时消息改为「结果未知，按原 key 对账」；`server.py` 只在写响应与 dispatch 两处吞 BrokenPipe/ConnectionReset/ConnectionAborted，内部异常和保存失败照旧 500；`camera._clean_english_prompt` 在截断、长度、中文等既有校验全过之后，按首次出现顺序折叠完全相同的逗号标签。

## Alternatives considered

全局把 `_request` 超时调大最强理由是改动最小、一处生效，但读请求本该快速失败，会被真不可达拖成长时间挂起并掩盖服务端故障，否决；客户端超时后自动补发最强理由是用户不必自己猜结果，但可能生成并保存第二张图、绕过 `operation_key` 幂等，只能如实报未知，否决。

## Consequences

固定检查 9 例通过（生产 `phone_handler` + 真 loopback `PhoneHTTPServer` 延迟响应、一次受控 RST 断连、标签折叠/保留/截断拒绝），既有相机 12 例仍绿；代价是相机最坏要等约 8 分钟，真实新图同步返回与转换保真未复验；测试临时根改为保留在磁盘（含首版误删偏差，见工单结果）。
