# Agent Note: 已提交 CONTINUE/START 与 writer 的 no_change 在同一读面可读

Status: implemented

## Problem

AG-1 已经**正式提交**一条 `CONTINUE`（或 `START`），内容 writer 随后合法回答
`no_change`（`model_continuation_writer.ModelNoChange`）。这既不是模型选了
`NO_ACTION`，也不是写成功。真实短日里出现过三次 `CONTINUE` 后 wake 只剩
`outcome=NO_ACTION`、`decision` 为空、`decision_route` 为空，运行摘要读起来像
"模型明确选择安静"。

最短链在 `AutonomousLifeCycle.evaluate_once`：`policy` 的 `base.update`（写入
`decision` / `decision_id` / `decision_route` / `selected_candidate_ref` /
`reason_codes`）发生在 `run_decision_and_adoption_cycle` **正常返回之后**。但
`ModelNoChange` 是从 coordinator 内部懒物化 step spec（`_DecisionBoundStepSpec` →
`_next_content`）时抛出的，异常冒泡到 cycle 的 `except` 时 `base` 还没有这些字段，
`_work_no_change` 于是只留下匿名 `NO_ACTION`。已提交的 DecisionRecord 就在
`pre_adoption_mutation_hook`（`_pause_gate_hook`）手里，但没有被这一轮记住。

## Decision

- `_pause_gate_hook`（AG-2 的 `pre_adoption_mutation_hook`，恰好是 Decision 提交后、
  采纳前）把本轮已提交的 `DecisionRecord` 存到 `self._round_decision_record`；每轮
  `evaluate_once` 开头重置为 `None`，失败轮不会沿用上一轮。
- `_work_no_change` 先调 `_carry_committed_decision(base)`：只填补 `base` 里**缺失**的
  `decision` / `decision_id` / `decision_route` / `selected_candidate_ref` /
  `reason_codes`，值全部取自那条已提交记录本身；已存在的值不覆盖，也不从 outcome
  名称反推。直接端口路径本来就有这些字段，行为不变。
- 合法 `no_change` 不再写成 `last_error`（它本来就不是错误，且 `ModelNoChange` 的
  文本携带模型自己的 self-note，不该进诊断读面）。
- outcome 仍是 `NO_ACTION`（不改枚举，保持兼容）；`work_no_change` 原样保留。
- runner 的 wake `extra` 增加既有字段 `decision_id`，以及 `work_no_change` 的
  定型投影（`reason_code` + `owner_called` / `version_created` 两个布尔，绝不带
  self-note 或正文）。
- resident `_last_due_view` 透传 `decision` / `decision_route` /
  `owner_version_created`；counters 增加 `no_action_with_decision`（仍计入
  `no_action` 总数，只把"决定 CONTINUE、执行无新版本"与"模型选 NO_ACTION"分开）。
- `scripts/g1_real_adoption.py` / `scripts/g1_integrated_g.py` 的安全 wake 白名单补上
  `decision_id` 与 `work_no_change`（只认枚举/布尔/id 形状，未知形状丢弃）。

## Alternatives considered

- **直接用 outcome 名称判断**：最强理由是零改动。否决：同一个 `NO_ACTION` 既可能
  来自模型的明确选择，也可能来自已提交 `CONTINUE`+writer `no_change`；按名称判断
  正是这次误读的来源。
- **让 coordinator 在异常里把 `out` 一并带出**：表达直接。否决：那要改 AG-2 的对外
  返回契约（新增异常载荷/新异常类型），超出本轮"补现成字段"的边界；现有
  `pre_adoption_mutation_hook` 已经拿到了同一条已提交记录。
- **新增 `NO_ACTION_WITH_DECISION` 之类的 outcome 枚举**：读起来最清楚。否决：会扩
  状态枚举，影响所有下游按 outcome 穷举的读面；任务要求保持原 outcome 兼容。
- **不投影 `work_no_change`，只靠 `outcome` + `owner_version_created`**：改动更小。
  否决：那样只说明"没写"，说不清"是 writer 合法回答无变化"，而 `work_no_change`
  本来就是现成的执行结果投影。

## Consequences

- 同一 wake 记录同时可读：`decision=CONTINUE`（或 `START`）、`decision_route`、
  `decision_id`、`selected_candidate_ref`、`outcome=NO_ACTION`、
  `extra.work_no_change={reason_code: NO_CHANGE, owner_called: False,
  version_created: False}`、`extra.owner_version_created=False`。
- 模型真正的 `NO_ACTION`（`reason_codes=[NO_ACTION_CHOSEN]`）保持
  `decision=NO_ACTION`，不会被重标；规则空集仍是 `RULE_RESOLVED` + 0 次模型调用。
- owner 版本 / Action / receipt / M0 不因"决定 CONTINUE"而虚增；`llm_calls` 仍只计
  正式决定轮，内容请求另占预算。
- 定向验收：`tests/test_decision_nochange_projection.py`（真实 Supply owner、
  `ProductionLifeRuntime`、正式 runner，脚本 cognition/content 明示）修前红、修后
  `EXIT=0`；相邻读面 `test_resident_projection_counts`、`test_model_continuation_no_change`、
  `test_observation_candidate_supply.ObservationCountingTest` 绿。未跑 G1/G2/全库。
- 与既有口径一致：[[2026-10-23-observation-count-rule-noise]]（按 route 计数、不按
  outcome 名称）、[[2026-10-26-v04-day-life-core-continuity]]（no_change 是合法明确
  未写，不强制 commit、不清 TTL）。
