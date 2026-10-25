# Agent Note: gate 关掉时"没发"必须报成拒绝，不能报成未决

Status: implemented

## Problem

fastday-d（`run:fastday-d`，2026-10-24 00:32 CST 决策）里，一次 POSE 出门被
`WORLD_BODY_INTENT_MUTATION_GATE=off` 挡住，但 Life 侧读出来的结果是一团"不知道"：

* 进程环境里没有 `canonical`，Intent Executor 在铸造一次性授权**之前**就返回
  `STATUS_REJECTED / GATE_OFF`；gateway、owner、world_state 都没被碰过。
* Life 的 World adapter 把它正确映射成 `rejected` +
  `DEFINITE_PRE_SUBMISSION_FAILURE`；AR-0 也正确地把 Action 留在 `PREPARED`、
  attempt 记成 `PRE_SUBMIT_FAILED`（同一个 action id 可重试）。
* 但 `autonomous_life_cycle._classify_world_step` 只认 Action 的
  `SUCCEEDED/FAILED/UNKNOWN`，`PREPARED` 落到兜底的 `STEP_UNSETTLED`。于是一次
  明确的"没发"被写成"可能发生了什么"，随后同一 Activity 的 CONTINUE 只得到
  `NO_STEP_AVAILABLE`，看起来像出门卡死。
* AR-0 的 `WorldActionAdapter.submit` 对预提交失败没有分支：它仍会构造一张
  "ACCEPTED"假回执（随后被 AR-0 丢弃），并把 resolver 的 `code` 丢掉，attempt
  的 `error_class` 为 `null`，用户无法从账本里看到 `GATE_OFF`。

## Decision

1. **预提交失败保留原因**：`action_reality_ledger.WorldActionAdapter.submit` 对
   `TRANSPORT_ERR_DEFINITE_PRE_SUBMIT` 单独返回，不造回执，把 resolver 的
   `code` 放进 `error_class` / `error_ref`。授权、TTL、幂等、Action 状态机不变。
2. **分类按既有 refusal 结算**：cycle 只在
   `Action=PREPARED && attempt=PRE_SUBMIT_FAILED && transport=DEFINITE_PRE_SUBMISSION_FAILURE`
   这一组精确证据下报 `OUTCOME_STEP_REFUSED`，并带
   `pre_submit={reason_code, transport_result, not_sent: true}`。fenced/submitted、
   缺 attempt、终态 Action 仍走原来的 `STEP_UNSETTLED` / UNKNOWN 路径；UNKNOWN
   的对账与不重发语义完全不动。
3. **显式 demo 贯通 gate**：`scripts/fast_day.py` 新增
   `resolve_world_intent_gate()`：未设置/空 = 默认 `off`；显式
   `off/shadow/canonical` 规范化后写进**resident 子进程自己的 env**；非法值在起
   任何子进程前具名拒绝。监督进程的 `os.environ` 不被修改，run.json 记录值与
   来源。默认权限没有被提高。

## Alternatives considered

* **把 `PREPARED` 一律当失败**：会破坏"预提交失败可用同一 action id 重试"的
  既有 AR-0 语义，也会把真正 fenced-but-unknown 的提交误判成失败。否决。
* **直接把 Action 从 PREPARED 改成 FAILED**：需要改 Action 合法矩阵/新增结算
  协议，超出"沿用既有 failure/refusal"的范围，且会放弃可重试性。停报，不做。
* **在 fast_day 启动时默认打开 canonical**：把部署配置问题变成权限提升，违反
  默认 off。否决；只透传操作者显式给出的值。
* **把 gate 值写进全局 `os.environ` 让所有调用共享**：会污染同进程的其它 run，
  且无法表达"本 run 的选择"。否决；只传子进程 env。

## Consequences

* gate 关闭时，账本/回合报告现在能看到 `GATE_OFF` 和 `not_sent: true`，而不是
  一个无法定位的 UNKNOWN；零票、零 owner 调用、零世界变化。
* 历史 d 实例不迁移、不重跑；它的旧 attempt `error_class=null` 保持原样。真实
  owner 的 UNKNOWN/丢回执仍按 [[2026-10-17-world-journey-ambiguous-submission]]
  对账，绝不重发；多段许可仍按
  [[2026-10-23-world-journey-segment-authorization]] 每段各铸一张票。
* 未验证：真实模型自然采用、24h/G1、跨备份恢复；本单 0 产品调用。

## 2026-10-25 补正：正式返回形状

### Problem

上面第 2 条的"真实链"验证只到 resolver；分类器本身只在**扁平夹具**
（`submit_result` 直接带 `action` / `attempt`）上测过。生产
`LifeIntegrationCoordinator.execute_activity_step` 返回的 `submit_result` 是
AR-0 bridge 自己的输出，真正的 attempt 在
`submit_result.submit_result.attempt`。于是真实 gate-off 链里
`PREPARED + PRE_SUBMIT_FAILED` 从没被读到：round 仍落
`STEP_UNSETTLED`，runner 写 wake `UNKNOWN`，`owner_evidence` 也留成 `None`。
2026-10-24 的 k 实例只读核对（`20261024-k-refusal-classification-audit-result.md`）
把这个形状差定位为静态根因；旧结论"账本/回合能看到 GATE_OFF"对扁平路径成立，
对真实链不成立，此处纠正，不倒写旧失败历史。

### Decision

1. `_classify_world_step` 经同文件纯 helper `_step_submit_attempt` **只解一层**
   正式的 `submit_result.submit_result`，并保留既有扁平形状。attempt 必须与
   外层 Action 同一 `action_id`（双方都有 id 时），否则不跨层拼接；缺 attempt、
   坏结构、fenced/submitted、UNKNOWN/FAILED 终态仍走原 unknown/unsettled 路径。
   精确判据仍是 `PREPARED + PRE_SUBMIT_FAILED + DEFINITE_PRE_SUBMISSION_FAILURE`。
2. `_owner_evidence` 只在 round 已分类为 `STEP_REFUSED` 且同轮
   `pre_submit.not_sent is True` 时返回 `owner_version_created=False`；真实未知
   仍 `None`。这不是把所有 `STEP_REFUSED` 泛化，也不新增重试/对账。
3. `_classify_ar0_step` 不读 attempt，本轮没有同类缺口，未改。AR-0 账本、
   Action 矩阵、提交幂等、权限、默认 gate off 均未动。

### Consequences / 验证边界

* 新 `tests/test_world_refusal_result_shape.py` 同时覆盖扁平/嵌套正向拒绝、
  跨 action 拼接、缺 attempt/坏结构/fenced/终态负控，以及 owner 证据。并在
  隔离临时根里走真实 production runtime → AG-2 `execute_activity_step`
  （真嵌套返回）→ 真 World gateway/registry：gate off 经 runner wake 得
  `BLOCKED`/`STEP_REFUSED`、Action 仍 `PREPARED`、attempt
  `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执/零票/零 travel/世界不变、
  `owner_version_created=False`、不入 UNKNOWN 对账；canonical 同链真出发
  `IN_TRANSIT`、一票消费、一条 travel，证据仍 `None`。
* `tests/test_world_dispatch_block.py` 只补嵌套形状回归，原扁平夹具与断言保留。
* 零产品模型调用；Decision/内容（冒烟里还有观察候选）是脚本替身。未证真实
  模型自然采用、G1/24h；k 的 CONTINUE 无步是独立缺口，本单不碰，也不重跑 k。
