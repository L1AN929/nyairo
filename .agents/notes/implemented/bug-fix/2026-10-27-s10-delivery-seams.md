# S10 F113-F115：受理后丢答保留阶段，不压成无阶段 UNKNOWN

Status: implemented

## Problem

`_project_kinds` 把 `STATUS_UNKNOWN`/`TRANSPORT_ERROR`/`DELIVERY_FAILED` 一律判成 `UNKNOWN_PROVIDER_STATE`，即使同动作已有 `PROVIDER_ACCEPTED`；`REASONS` 里已定义的 `UNKNOWN_AFTER_ACCEPT`/`UNKNOWN_AFTER_SUBMIT` 永远不产出，受理后丢答与提交前未知在可读事实里不可区分。

## Decision

`_unknown_reason` 只按已观测 kind 选既有 reason（`PROVIDER_ACCEPTED`→`UNKNOWN_AFTER_ACCEPT`，`SUBMIT_OBSERVED`→`UNKNOWN_AFTER_SUBMIT`，否则保持 `UNKNOWN_PROVIDER_STATE`），状态仍 `UNKNOWN`，不新增枚举/持久字段/重试；首版曾给 `ContactActionResultProjection` 加 `reconciliation_reason` 并在 `handoff_facts` 输出，因改对外形状被退回，本版按具体 delta 移除（投影文件与 HEAD 一致），demo/tests 改从既有 reconciliation record 的 `reason` 读取。

## Alternatives considered

**给投影新增 `reconciliation_reason` 让消费方直接拿到阶段**：最强理由是省一次 reconciliation 读取、读面最直观；否决，`ContactActionResultProjection`/`handoff_facts` 是既有对外形状，为一条 reason 加键超出修漏接范围，阶段信息在 reconcile record 既有 `reason` 里已完整可得。

## Consequences

验收：父级固定 demo exit0，accepted-then-lost/after-reopen 为 `UNKNOWN_AFTER_ACCEPT`、lone 为 `UNKNOWN_PROVIDER_STATE`，本单 6 例 OK；代价是消费方需读既有 reconciliation 输出，未证真实消息/phone/Life 接线。
