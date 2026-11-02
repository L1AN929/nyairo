# Agent Note: 只认本件 Action 的真实回执作为活动进度（F-167）

Status: implemented

## Problem

LR-5 消费 AR-1 结算事件时只信事件自述的 `activity_ref`/`target_ref`：它会从 `settlement_read` 取结算记录，却从不核对记录里的 `action_id` 与事件是否同一件，也不核对事件是否真由 AR-1 发出。另一 Activity 的真实结算事件只要被改写成 `activity_ref=受害活动` 且 target 命中绑定，就会被当成本活动证据消费；设计 VIII.13 Q44/Q48 明确要求 consumer 去权威读口核实、不能相信传入 result 自述，收据按 action/obligation 引用而非前缀。

## Decision

`ActivityProgressCoordinator._authoritative_settled_event_rejection` 在 ingest 前用既有 `SettlementReadService` 核对：存储事件必须存在，`action_id`/`settlement_ref`/`activity_ref` 与传入事件一致，结算记录也必须指向同一 `action_id`。不一致就记 `unbound_evidence_log` 并返回 IGNORE 类理由，零绑定/零 checkpoint/零完成；拒绝不写幂等索引，未篡改的原事件之后仍可正常消费。

## Alternatives considered

只信事件里的 `activity_ref`（现状）最强理由是“AR-1 是唯一生产者，事件在 outbox 里就是真的”；否决：设计把“不能相信传入 result 自述”定为 consumer 义务，且演示已证明改写后的事件会被接受，只靠 LR-2 前台门偶然挡住写盘。另一个方案是让 LR-5 加 AR-0 action 读口按 action→activity 反查，最强理由是能挡住 target 也相同的跨活动错绑；否决：需要新查询口/新写集，本轮按停止门不做，留作已知边界。

## Consequences

只有 AR-1 权威事件与结算记录一致的 receipt 才算进度，跨 Activity 改标签和伪造事件 id 都被拒且无副作用；未篡改事件仍可消费、重投仍幂等。验证：`scripts/infra_source_binding_demo.py` 真实 Supply/runtime 双活动 16 项检查、`tests/test_infra_source_binding.py` 6 例、`test_artifact_closeout_v04` + `test_infra_consequence_settlement` 26 例通过；Decision/内容为脚本替身，target 也相同的跨活动错绑仍需 AR-0 读口，未覆盖。
