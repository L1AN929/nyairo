# Agent Note: 模型调用按 Decision route 计数，规则空候选不再伪装成模型安静

Status: implemented

## Problem

压缩日 b 跑完 24 逻辑小时后，`resident_summary.counters.model_calls` 与
`model_calls_detail.cognition_calls` 都是 8，但持久请求账本只有 7 次预留
（4 decision + 3 content）。差的 4 次来自 AG-1 的本地规则回合：空 CandidateSet /
全硬拒按契约走 `RULE_RESOLVED`（0 LLM），cycle 却在 `NO_ACTION`/`DEFER` 分支一律
`_finish(llm_calls=1)`，于是 8 个实质 wake 被读成 8 次模型调用。同时 wake 的
`detail` 只有 `decision=NO_ACTION`，外部无法区分"本地规则判定没有合法候选"与
"模型看过之后选择安静"；horizon 正常结束时业务时钟还停在 `RUNNING`，与已退出的
authority、`horizon_remaining=0` 并存，像还能继续。

## Decision

- `AutonomousLifeCycle` 的 `NO_ACTION`/`DEFER` 出口按**已提交 Decision 的
  `decision_route`** 计调用：`RULE_RESOLVED` 为 0，其余为 1；`_finish` 对同一 route
  再做一次钳位，任何出口都不会把本地规则记成请求。
- 该出口的 `detail` 写明 `route=... reason=...`；runner 的 wake `extra` 新增
  `decision_route` / `reason_codes` / `selected_candidate_ref`，全部取自 Decision
  记录本身，没有时给 `None`/空列表，不从 outcome 名称反推。
- resident 汇总新增 `rule_resolved_windows`（本地规则窗口），`model_calls_detail`
  增加 `decision_rounds` 与 `count_semantics`；请求总数仍只认持久 request ledger，
  不为"成功往返"造计数——传输未知仍是未知。
- `scripts/fast_day.py` 在逻辑 horizon 用完时调用既有 `authority.stop()`，时钟落
  `STOPPED`；未到 horizon 的短窗口保持 `checkpoint`/`RUNNING` 与同 run 重启语义。
  没有新增状态枚举。

## Alternatives considered

- **在 resident 汇总时用 outcome 名称扣减**：最强理由是不动 cycle。否决：同一个
  `NO_ACTION` 既可能来自模型也可能来自本地规则，按名称扣减会把真实模型回合也吞掉，
  且账本对不上时无从判断；route 是 Decision 记录里已有的权威字段。
- **把 4 次规则窗口记成"零请求 wake"但不标来源**：改动更小。否决：数字对了，但
  "为什么安静"仍不可读，后续仍会把合法空候选误当供给故障或误当自主安静。
- **horizon 后新增 `COMPLETED`/`HORIZON` 时钟状态**：语义更直白。否决：会扩状态枚举和
  所有读面/重启判断；既有 `STOPPED` 已经表达"这个 run 不再跑"，`start` 也会拒绝重开。
- **把 `RUNNING` 留给 clock-only 冒烟以免改旧测试**：否决：真实日与冒烟用同一终点语义
  才诚实；旧的重启用例已改成"未到 horizon 的短窗口"，并新增"到 horizon 后重开被拒"
  的负控。

## Consequences

- 账本仍权威：决策轮次、请求预留、失败/重试不再混成一个数；规则窗口单列。
- `tests/test_observation_candidate_supply.py` 用真实 cycle/runner 跑 4 次
  `COGNITIVE_REQUIRED` + 4 次 `RULE_RESOLVED`，合计必须是 4 次模型决策轮；`NO_ACTION`
  与 `DEFER` 各按 route 计数，失败路径仍记 1 次尝试；并核对 `NO_LEGAL_CANDIDATE`
  标注与 resident 汇总。计数语义是**模型决策轮/尝试**，不是实际 HTTP 次数，也不把
  账本预留当成一次成功往返。
- World 供给的整链证据用真实 owner：真实 `WorldBodyService`（自己的 gateway socket、
  world home、`daily_neighborhood_v1` 世界包）+ 真实 `LifeSupplyService` 建实例，
  `resolve_world_provider(clock=业务钟)` 得到正式 `WorldMoveOpportunityProvider`，
  真实 cycle 刷新 → 真实 `WorldOpportunityCandidateSource` → 生产 runtime 的候选读口。
  业务锚点先于准备结束（与 b 同构），快进超过真实+300 秒仍出合法 `place:street_front`
  候选；旧裸调/墙钟盖章在同一场景仍分别是 `OPPORTUNITY_NOT_YET_OPEN` /
  `OPPORTUNITY_WINDOW_EXPIRED`，撤权后目标缺席、重复刷新不重放、无 World 配置具名
  `UNAVAILABLE`。测试里手造的机会只保留为 AG-0 窗口负控，不再当作来源证据。
- `tests/test_fastday_runtime.py` 核对 horizon→`STOPPED`、重开被拒、未到 horizon 仍
  `RUNNING`；`scripts/observation_candidate_smoke.py` 从 CLI 展示上述结果。
- 未改 AG-1 路由策略、预算上限（96/300）、账本语义与真实 b 的历史数据；模型不参与
  这条链，真实模型自然采用仍未证。
