# Agent Note: 修后组合唯一新批 A（复用 idle2 薄入口，不复制仪器）

Status: implemented

## Problem

三处修复已各自独立验收：已提交 Decision 与 writer `no_change` 同轮可读
（[decision-nochange](../bug-fix/2026-11-06-decision-nochange-projection.md)）、
World 已落盘到达不再误报 `ARRIVAL_REALIZATION_FAILED`
（[arrival-boundary](../bug-fix/2026-11-06-world-arrival-realization-boundary.md)）、
GO_TO 出发后 readiness 不再谎报还有一步
（[no-further-step](../bug-fix/2026-11-06-world-move-no-further-step.md)）。
需要一次**真实组合用法**看它们在同一个正式 run 里能否同时成立：提交 MOVE 后
travel/journey/Activity 收尾是否各自成立、同一 Activity 的
`CONTINUE → NO_STEP_AVAILABLE` 是否消失、`CONTINUE`+`work_no_change` 是否同轮可读、
idle 的 offered/selected/completed 是否仍按正式字段计数。

旧 idle2 入口把身份、额度、root、authority、run/domain、时限全写成模块常量，无法在不
复制整套 harness 的前提下开第二批；直接改常量又会污染原批默认值。原
[safe_observation](2026-11-06-idle2-bounded-observation.md)只投影 idle/reading，
不回答 GO_TO 的 step/action/travel/journey 关联。

## Decision

1. `scripts/idle2_bounded_run.py` 增加不可变 `Identity` dataclass 与 `IDLE2` 实例；
   `configure/status/prepare/run` 接受显式 `identity` 参数，**默认仍是 `IDLE2`**，
   所有原常量（root/authority/run/domain/490/1000/20/300/7200/86400/12/时限）逐值保留。
   `agency_view()` 从 `safe_observation` 里抽出，读面与合并规则不变。
2. 新增 `scripts/combined_observation_a.py` 薄入口，固定
   `root=/tmp/nyairo-combined-natural-20261106-a`、
   `authority=/tmp/nyairo-combined-natural-20261106-a-memory-authority/authority.db`、
   `run:combined-natural-20261106-a`、`fastday:combined-natural-20261106-a`、
   history 522 / 累计 1000 / 新增 20 / UTC 300 / 最坏 542、7200 真实秒 / 86400 逻辑秒 /
   rate 12 / 08:00 逻辑锚、22:30 最晚启动 / 00:50 截止新请求 / 60 秒收尾。
   CLI 只有 `offline-check/prepare/audit/run/status`，不接受任意 root/run 覆盖；
   prepare/run 复用原新鲜根、最晚启动、已 start、failstop 守卫，重复调用前拒。
3. 组合投影只在新 wrapper 做一次只读元字段拼接：
   * `decision` 与 `outcome` 分两层。Decision 取已提交记录/同一 wake 的
     `decision_id`、`decision_route`、`selected_candidate_ref`；outcome 单独取
     `outcome` 与 `extra.work_no_change`。模型 `NO_ACTION_CHOSEN` 与规则
     `RULE_RESOLVED + NO_LEGAL_CANDIDATE` 分别计数，不从 outcome 反推 Decision。
   * `go_to` 按 `activity_ref`/`decision_ref` 关联 GO_TO Activity 与正式 Decision；
     step 只认 `step_readiness.activity_ref` 完全相同的 wake；动作只从 ActionStore 中
     `activity_ref` 相同的记录取（没有才退到同一 activity 的 wake），最后一条无关 wake
     不再借给当前 Activity。
   * Activity 与旅程必须走已存在的正式桥：Activity →
     `life_integration_state.json` 的 `integration.action_refs` →
     `action_reality_state.json` 中同 `activity_ref` 的 `Action.idempotency_key` →
     `world_journey.active.request_key`。整条链对上才把 journey/segment/travel/arrived
     贴给该 Activity（`activity_journey_join=SAME_ACTIVITY`）；缺任一段则 `go_to` 下这些
     字段保持 UNKNOWN，并报 `activity_journey_join` 的具体缺口。
   * 世界旅程另报顶层 `world_journey`：同名 `execution_id` 的 travel `ARRIVED` 且旅程
     `completed` 才为真；位置相同、别的 execution、别的 request_key 都不算。世界旅程为真
     不等同于该 Activity 到达。
   * Activity 完成只认 `COMPLETED` 且带 `completion_evidence_ref`，与到达各报各的。
   * Decision id 只取本轮 `wake.extra.decision_id`（正式 runner 的真实形状），再回
     `decisions_by_id` 核存在/route/selected refs；wake 里的 route/selected 只当诱饵，
     不覆盖已提交记录。AG1 snapshot 与 journal 的 `state_delta.map_updates` 一起读。
   * GO_TO 只取 revision 最高的一个，输出 `scope=LATEST_REVISION_GO_TO_ONLY` 与有界的
     `candidate_count`，不冒充全日总计。
   * `_idref` 接受真实 `actv_...` Activity id；`idle2_bounded_run._safe_id` 只把同一
     `actv_` 形状加入安全白名单，默认身份与其余行为不变。
   * `same_activity_continue_no_step` 从 wake 的 `activity_ref/decision/outcome` 现成
     字段计数；idle offered 仍只取 AG1 accessibility 精确候选键，materialized 另计。
   * reading 复用原 `safe_observation` 的既有枚举与 `owner_version_number`，只补
     `extra.owner_version_created`；缺字段或未触发为 UNKNOWN。
   所有输出只含固定 ID/枚举/计数/时间/布尔，不读 prompt/completion/reasoning/
   short_rationale/正文/title/endpoint/异常正文。
4. 新增仓库根目录的 `20261106-combined-a-prepare.sh` 与 `20261106-combined-a-run.sh`，
   只写未执行；run shell 只由父级 source `/root/.nyairo-model.env` 并在缺失/占位前拒绝，
   不回显 endpoint。
5. 原 idle2 note 只补一条互链，不改其正文结论。

## Alternatives considered

* **复制整份 idle2 入口成新文件**：最强理由是零回归风险、改起来最快。否决：会把预算
  包装、failstop、安全投影、守卫全部复制一遍，两个入口立刻漂移；任务明确要求复用薄入口。
* **运行期 `mock.patch` 模块全局 `ROOT/RUN_ID` 换身份**：最强理由是不用改任何签名。
  否决：身份不是运行期行为而是持久配置，patch 只影响当前进程的常量引用，落盘/子进程/
  账本仍可能读到旧值，且容易漏改；改为显式不可变参数，默认值不动。
* **把组合投影也塞进原 `safe_observation`**：最强理由是只有一个读面、调用方简单。
  否决：原批已验收的投影形状会变，旧批读面跟着漂移；新批的 GO_TO/Decision 组合属于本批
  观测需求，放在新 wrapper 才符合“旧默认不漂移”。
* **按位置或候选数量推断到达/offered**：最强理由是字段少、实现短。否决：位置相同不等于
  同 execution 的 travel 已 ARRIVED，candidate_count 不等于 allowed accessibility 的
  offered；这正是旧批误读的来源。
* **直接把 active journey 的 travel 到达贴给 revision 最高的 GO_TO**：最强理由是两者在
  同一次 run 里经常同时出现、实现最短。否决：没有对象级桥，另一个 Activity、另一个
  Action 或另一条 request_key 的旅程同地到达都会被误记，这正是父级指出的串对象。
* **为 Activity→journey 新造 owner 字段或产品日志**：最强理由是一次 join 到位、读面简单。
  否决：现有 AG2 `integration.action_refs`、AR-0 `Action.idempotency_key` 与
  `journey.request_key` 已经能精确对上同一次请求；新字段会改产品落盘契约并扩大写集。
* **为验收跑旧 idle2 全 8 例/G1/G2/全量**：最强理由是覆盖最全。否决：本批只改参数接线与
  新读面，按项目“验收只做冒烟”规则，只跑本批 4 个聚合 case。

## Consequences

`offline-check` 4 个聚合 case（新身份与新 scratch 正式 prepare → 纯读 audit 零账；
原守卫少量点验；组合投影的完整桥正控、另一个 Activity/Action/request_key 同地或更高
revision 的负控、无桥 UNKNOWN、不同 execution 不混、隐私哨兵；旧 idle2 默认不漂移）在
AF_INET/INET6/DNS/create_connection 硬拒、AF_UNIX 放行的守卫下全绿，固定命令 exit=0、
4 例、0 skip，正式根未被触碰、产品调用 0。测试里的 `dec:one` 只写在 AG1 journal 的
`state_delta.map_updates`，`wake.extra.decision_id` 与 `actv_...` Activity id 都按真实
形状断言。

代价与未证：真实 provider 的自然链、自然 idle 选择、因果效应、整段 7200 秒逻辑日都
**未验证**；本批只证明入口接线、守卫与读面形状。产品新增请求 0，历史 522 不变；正式
run 只能由父级执行，本单未 source 模型环境、未启动任何正式实例。scratch 全部保留在
`/tmp`，未提交、未推送。
