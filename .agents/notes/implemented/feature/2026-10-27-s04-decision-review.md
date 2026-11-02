# Agent Note: S04 决定摘要、未选/延期项与 Decision/Adoption 分离

Status: implemented

## Problem

S04 的 DecisionMade 已经落盘，但操作者没有一条可用的读面回答三个问题：

1. 她当时实际选了什么、候选来自哪里；
2. 既有记录里到底保存了哪些未选/延期项和理由代码；
3. 那条决定现在还用不用得上——但它只是历史，不能被读一下就自动执行。

手机与 CLI 此时只有 AG-1 的低层读口（`get_decision` / `get_candidate_dispositions`），
没有任何“有限摘要”的投影。若直接把这些通通倒出来，会同时踩三条线：把未持久化的
“她当时为什么不选”补写出来；把 `short_rationale` 当成可随便公开的正文；把
Decision 误当成 Adoption（读取即约束）。

## Decision

新增 `components/life/scripts/decision_review.py`，只做**纯读投影**：

- 摘要字段全部来自既有 `DecisionRecord` 与 AG-1 在提交时一并持久化的
  `candidate_dispositions_by_decision`。**不新增 AG-1 输出键、Decision 持久字段或新格式。**
- 记录里没有的字段一律输出 `NOT_RECORDED`。`short_rationale` 缺失时摘要退回
  `decision + reason_codes`，不生成一句“她当时想……”。
- 未选项按既有 disposition 分为 `NOT_SELECTED` / `DEFERRED` / `HARD_REJECTED` /
  `NOT_EXECUTABLE_NOW`；只展示记录里真有的，不推断影响程度或排序。
- Decision 与 Adoption 分开成三态：`DECIDED_NOT_ADOPTED` / `DECIDED_ADOPTION_REJECTED` /
  `DECIDED_AND_ADOPTED`。Adoption 状态只从 AG-2 自己的 `adoption_id_by_decision` 读；
  读坏了报 `UNKNOWN`，不当作“从未采纳”。
- 旧决定复核 `review_live_applicability` **不复制** AG-2 的准入规则：它接受调用方传入的
  正式 `validate_decision_for_adoption`（纯读），只转发结论与具名 reason code。没有正式
  读口时明确返回 `NOT_EVALUATED / NO_LIVE_ADMISSION_READER`，绝不从旧 snapshot 宣称
  `CURRENT_VALID`。即使复核通过也只表示可以重新走进既定流程，不继承旧许可。
- `short_rationale` 默认扣留：手机读面永远只报是否存在与长度；本地 CLI 需显式
  `--show-rationale` 才在操作者自己的终端显示。这不是隐私隔离承诺（不隔离同 uid 进程或
  管理员），只是读面默认值。
- 读面层：`components/phone/own_day_read.decision_review_envelope` +
  `GET /api/phone/decisions/review`（`?decision_id=` 单查，缺省列最近骨架），
  以及 `scripts/s04_decision_cli.py`。两者都只 `load_state`，不取 lease、不写任何 store。

隔离验收（真实 Supply/AG-1/AG-2/Activity owner，正式 `evaluate_once`，脚本 Decision/内容，
机械业务时钟）由 `scripts/s04_decision_review_demo.py` 覆盖；投影层回归见
`tests/test_s04_decision_review.py`。

## Alternatives considered

- **新增一份“决策解释”持久表，把未选理由、影响程度、事后解释都存下来。**
  最强理由：读面可以更“好讲”，用户看到的不只是代码和候选 id。否决原因：这正是设计
  Q5/Q15 与任务书写明的红线——未选候选可仅内存，重要取舍才留存；事后补写的理由会把
  “没记录”变成“她想过”。新增持久字段还会改 AG-1 的输出协议，超出本单边界。
- **在 review 模块里重新实现一遍“旧决定是否仍适用”的判断。**
  最强理由：CLI 只有 state root 时也能独立给出结论，不必装配 runtime。否决原因：那是
  第二套审批规则，会与 AG-2 的 revision/foreground/candidate/eligibility 检查漂移。
  正确做法是让调用方注入正式读口；没有就诚实报 `NOT_EVALUATED`。
- **把 `short_rationale` 默认当作可公开正文直接返回。**
  最强理由：它本来就是记录的一部分，手机端也是本人使用。否决原因：任务书明确“原 record
  包含 short_rationale 不代表可随便公开”；operator 读面只给骨架，正文改由本地 CLI 的
  显式开关承担。
- **把已有的 `explain_decision` 直接透出当作读面。**
  最强理由：零新代码，AG-1 已有。否决原因：它只返回 `honest_summary` 与 reason codes，
  不区分未选/延期，不报 Adoption 状态，也不区分 Decision 与 Adoption；直接暴露还会把
  rationale 正文带出去。

## Consequences

收益：操作者第一次能在不碰业务写入的前提下看清一条决定的全貌与边界；未记录与未知都被
具名，不会被读成“没有”或“她想过”；Decision 与 Adoption 的三种关系分开显示，读取不会
自动采用。

代价与边界：

- 普通未选候选不保证长期保存，读面只能展示 AG-1 当时真落盘的处置表；候选正文仍不在
  读面里（只给 ref/kind/source）。
- 手机侧不做实时适用性复核（`NOT_EVALUATED`），需要正式 runtime 的复核在 demo/运行链里。
- `short_rationale` 扣留只是默认值，不是权限隔离；能读 state root 的同 uid 进程仍可直接
  读原始记录。
- 本单不新增 AG-1/AG-2 的合同，也不证明自然模型采用或 G1；S09/S08 的写权完全未动。

相关笔记：[决定合法矩阵](../architecture/2026-10-26-v04-day-life-decision-legality.md)、
[S09 继续链](2026-10-27-s09-continuation.md)、
[PAUSED 显式恢复条件](2026-10-24-paused-explicit-resume-condition.md)。
