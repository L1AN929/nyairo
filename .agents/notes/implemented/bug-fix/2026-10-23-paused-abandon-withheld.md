# Agent Note: PAUSED ABANDON 出口在无法证明安全时扣留

Status: implemented

## Problem

上一单让 PAUSED 前台活动可以走既有 ABANDON 出口
（[2026-10-23-paused-abandon-existing-path.md](2026-10-23-paused-abandon-existing-path.md)）。
它证明了状态机允许这条转换，但没有证明**安全**：设计 2.0 只在活动没有未决副作用 /
承诺绑定时才把它判为 ABANDONED，而现有接口给不出这个事实。

- canonical Activity 记录没有 `commitment_binding` 之类的字段；
- AR-0 的 `ActionStore` 用 `actv_<id>`，canonical 活动用 `actv:<id>`，两套词表不相等；
  按活动查「有没有未决 Action」会静默漏掉所有真实绑定；
- `DecisionContextSnapshot.commitment_constraint_refs` 为空可能表示「没有」，也可能表示
  「没读」；现有接口无法区分。

`tests/test_paused_abandon_existing_path.py` 原用例 `test_abandon_keeps_unresolved_action_...`
还把这个缺口写成了「同活动的未决 Action 被保留」——实际上它提出的 Action
`activity_ref=None`，从来不是同活动证据。

## Decision

**扣留**。AG-1 不再把 `ABANDON` 放进 PAUSED 前台的合法集（`CURRENT_ACTIVITY` 与
`RESUME_ELIGIBLE` 两条投递路径都扣），`validate_decision_output` 对 PAUSED 前台的
ABANDON 直接拒绝；AG-2 在发出 LR-2 命令前再拒一次，具名原因
`PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE`。

没有删除引用、没有把 `actv:` 映射成 `actv_`、没有新建承诺合同或 Action id 兼容层、
没有自动 ABANDON。手机页面把这件事写成「她把活动停在这里，没有继续执行」，不承诺
它会自己继续。

## Alternatives considered

- **扩 id 词表 / 做映射后按活动查 AR-0**：最强理由是「这样就能真正证明没有未决
  Action」。否决原因是两套 id 的分隔符是各自 owner 的协议形状，映射会制造一个没有
  owner 的别名层；一旦映射错，危险方向是「把有未决副作用的活动当成没有」。这属于新
  数据契约，不能在手机交付单里顺手加。
- **把空 `commitment_constraint_refs` 当「无承诺」**：最强理由是快照确实是决策的授权
  视图。否决原因是空值与未读无法区分，读不到不能当没有。
- **保留出口但在 UI 上提示风险**：这会把不可证明的安全问题降级成用户提示，且模型仍可
  触发真实副作用，否决。

## Consequences

- PAUSED 前台目前没有执行形状的出口（CONTINUE / WAIT / PAUSE / RESUME 本来就需要
  ACTIVE 或资格）；这会让遗留实例停在原处，但不会伪造完成或扩大门禁。
- ACTIVE 前台的 PAUSE / ABANDON、ATOMIC、CAS、幂等、前台冲突、LR-2 admission 全部
  不变；`test_day_life_decision_legality` 里 PAUSED 自身候选的 ABANDON 断言同步收紧。
- 页面（`GET /api/phone/living`）如实显示 PAUSED / 停点，不把它写成「正在做」或「已
  继续」。将来若要恢复出口，需要先有一个 owner 级、可证的活动-未决副作用查询契约，
  再补正负控；不要从界面或映射层绕过去。
- **2026-10-24 后续规则**：PAUSE 现在释放执行槽，PAUSED 不再占前台。本笔记的
  `PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE` 安全门**不变**：AG-2 仍拒绝 ABANDON 一个
  PAUSED Activity，因为“没有未决副作用/承诺”仍无法从既有 owner 事实证明。LR-2 状态机层
  对释放后的 PAUSED 保留 terminal transition 能力（与 WAITING 释放后的旧规则并列），
  但没有新的正式采纳路径。见
  [PAUSE 释放执行槽](../feature/2026-10-24-paused-background-single-execution.md)。
