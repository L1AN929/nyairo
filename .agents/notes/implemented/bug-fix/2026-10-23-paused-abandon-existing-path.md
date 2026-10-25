# Agent Note: PAUSED 前台活动接通既有 ABANDON 出口

Status: superseded

> 出口在后续核对中被扣留：现有接口无法证明 PAUSED 活动「没有未决副作用 / 承诺绑定」。
> 见 [2026-10-23-paused-abandon-withheld.md](2026-10-23-paused-abandon-withheld.md)。
> 本文保留为「为什么下层允许这条转换」的历史记录，不再代表当前合法集。

## Problem

设计 2.0 分章 SRC-0719 第 12–13 行把 `PAUSED` 与 `READY / ACTIVE /
WAITING_EXTERNAL / BLOCKED` 并列，写明都可由相应 owner 判 `ABANDONED`；SRC-0007
第 16 行把等待、暂停、放弃列为合法生活选择 / transition intent。既有下层也一直允许：

- LR-2 `ActivityCommandService.abandon_activity` 的 `allowed_from_statuses` 是
  `(ACTIVE, PAUSED, WAITING)`（`activity_continuity.py:2548`），终态转换会释放
  `foreground_activity_ref`（`:2284-2286`）；
- AG-2 的 `DECISION_ABANDON` 分支只要求存在 target Activity，不按状态拒绝
  （`life_integration_ag2.py:3652-3696`）；
- `DecisionOutputValidator` 的 ABANDON 字段校验同样不要求 ACTIVE。

卡住的只有 AG-0 / AG-1 两层窄接缝：

1. `CurrentActivityCandidateSource.validate_source` 对 `PAUSED` 直接返回
   `ACTIVITY_PAUSED_REQUIRES_RESUME_ELIGIBLE`，`refresh` 也把非 ACTIVE 一律标
   `STALE`。PAUSED 前台根本不产出候选。
2. `HardConstraintEvaluator` 的 live Activity 检查把 `act_status != ACTIVE` 一律
   拒为 `act_not_active`；`compute_legal_decisions_for_candidate` 的
   `CURRENT_ACTIVITY` 分支也只在 `cur_status == ACTIVE` 时给 `PAUSE / ABANDON`。

结果是：一个没有 OPEN `ResumeEligibility` 的 PAUSED 活动，模型既没有候选，也没有
合法出口。B1 只对齐了「PAUSED + OPEN 资格」的 `RESUME`
（[2026-10-23-paused-resume-eligibility-alignment.md](2026-10-23-paused-resume-eligibility-alignment.md)），
它明确不制造资格、不解遗留实例；本单补的是同一状态机上另一条既有出口，不替代 B1。

## Decision

只改 AG-0 与 AG-1 的投影/合法集，不改 AG-2、LR-2、LR-4、存储格式、候选种类或
决策词表：

1. `candidate_sources_ag0.py` 的 `CurrentActivityCandidateSource` 对**当前前台自身的
   `PAUSED`** 继续发射既有的 `CURRENT_ACTIVITY` / `CONTINUE_CURRENT` 投影：
   `validate_source` 放行 `PAUSED`，`refresh` 在 `ACTIVE` 或 `PAUSED` 时保持 `OPEN`。
   仍然是「只读 LR-2 前台、只发这一个活动、0 写入」；`WAITING`、`INTERRUPTED`、
   terminal、以及任何非前台活动照旧不发。没有新建 candidate kind / source / schema。
2. `agency_decision_ag1.py` 的 `HardConstraintEvaluator` 对 `SOURCE_CURRENT_ACTIVITY`
   增加一条**只针对快照当前前台**的例外：live `status == PAUSED`，候选的
   `activity_ref == context_snapshot.current_activity_ref`，且快照自己也说
   `PAUSED`。revision 一致性检查保留，其余非 ACTIVE 状态仍拒。
3. `compute_legal_decisions_for_candidate` 新增 PAUSED 分支：对「当前前台自己的
   `CONTINUE_CURRENT` 候选」只追加 `ABANDON`（`interruptibility != ATOMIC`），
   `DEFER` 是函数入口本来就有的。`CONTINUE / WAIT / PAUSE / RESUME / START` 一个
   都不给；ABANDON 不看 `exec_now`，与既有「暂停/放弃与预算解耦」一致
   （[2026-10-26-v04-day-life-decision-legality.md](../architecture/2026-10-26-v04-day-life-decision-legality.md)）。
4. `validate_decision_output` 的 ABANDON 分支只加一条防伪造：快照当前前台是
   `PAUSED` 时，选中候选的 `activity_ref` 必须就是该前台，别的活动在进入 LR-2
   之前就被拒。

ABANDON 本来就是既有决策词，AG-2 的采纳路径、LR-2 的 admission/CAS/幂等、ATOMIC
检查、前台释放、experience handoff 全部原样复用。本单不生成 eligibility /
condition / agenda，不自动放弃，也不替模型选择。

## 边界与不声称的事

- **Q10 的 S08 assessment 义务仍未接线。** 现有 Activity 记录没有
  `commitment_binding` 字段，AG-2 在 ABANDON 后只发 experience handoff
  （`life_integration_ag2.py:4961-4991`），没有 `ActivityAbandoned → S08 assessment
  obligation`。本单不新增承诺绑定或后果合同，所以这条出口只对**没有承诺绑定**的活动
  成立；带承诺的活动放弃后如何评估 `AT_RISK / NEEDS_REPLAN / NEEDS_RENEGOTIATION`
  是具名缺口，不能声称 Q10 已经守住。
- **未决 Action 无法绑到同一 Activity。** AR-0 的 `ActionProposal` 要求
  `activity_ref` 以 `actv_` 开头（`action_reality_ledger.py:624-626`），而 AG / LR-2
  的 canonical refs 用 `actv:<id>`。两套拼写不相交，本单不扩任一词表；测试只能证明
  ABANDON 不结算、不改写一条已经存在的 PROPOSED Action。
- **没有证明自然可达性。** 模型 Decision 是脚本替身（EL3 限定机制），不声称真实模型
  会自然选择 ABANDON，也不声称遗留的 PAUSED 实例会被放弃。G1 未通过。
- 不运行主实例、不调用产品模型、不改脏数据、不提交。

## Alternatives considered

1. **只改 AG-1 合法集，不动 AG-0。**
   最强理由：改动面最小，只碰决策层，不扩候选投影。
   否决：PAUSED 候选在 AG-0 就被拦掉，AG-1 的合法集根本没有输入；只改一层等于没改。
2. **为 PAUSED 新建 candidate kind / source。**
   最强理由：语义最直白，「可放弃的当前活动」不必复用 CONTINUE 的名字。
   否决：既有 `CURRENT_ACTIVITY` 投影已经携带身份、来源、revision 与前台绑定；
   新种类要同步 schema、prompt、存储与消费者，属于本单禁止的契约扩张。候选名字只是
   投影，AG-1 才是决定能否执行的层。
3. **同时给 PAUSED 的 `CONTINUE / WAIT / PAUSE`。**
   最强理由：模型选择面更大，看起来更像完整状态机。
   否决：PAUSED 明确是暂缓，不能继续或再暂停；WAIT 需要具体外部依赖。设计只给了
   「满足恢复条件后回来」和「owner 判 ABANDONED」两条路，其余属于无依据放行。
4. **对遗留实例自动 ABANDON。**
   最强理由：现场实例正卡在 PAUSED，自动收尾能立刻解卡。
   否决：放弃是主体的选择；owner 允许放弃不等于系统可以替她放弃。这也会绕过模型与
   AG-1/AG-2 的完整决策链。
5. **顺手在 AG-2 补 Commitment assessment。**
   最强理由：一次把 Q10 也接通，避免只做半条设计。
   否决：需要新增承诺绑定字段与后果合同，属于任务书的停止条件；不能在同一个窄接缝
   工单里私扩写集。

## Consequences

- 当前前台的 PAUSED 活动重新出现在候选集里；模型的合法选择是 `ABANDON` 或 `DEFER`，
  选中 ABANDON 时走真实的 AG-1 validator → AG-2 预授权 → LR-2 终态，前台释放，
  `terminal_reason_code = ABANDONED_BY_DECISION`。
- ACTIVE 行为的合法集不变（仍有 `CONTINUE / PAUSE / ABANDON`）；WAITING 仍不发射
  候选；terminal 活动仍不可复活或重复终结；ATOMIC 仍不给 ABANDON。
- PAUSED 的恢复路径没有被削弱：B1 的「PAUSED + OPEN eligibility → RESUME」原样保留，
  本单不制造任何 eligibility。
- 没有承诺绑定的活动才有完整语义；带承诺的放弃仍需 S08 后果合同，未交付。
- 新套件 `tests/test_paused_abandon_existing_path.py` 11 例；直接相关的旧合法集与 B1
  回归 26 例通过。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_paused_abandon_existing_path`
  —— 11 例通过。真实 LR-2 / LR-4 / AG-0 / AG-1 validator / AG-2 adoption，隔离临时根；
  Decision 内容与准备阶段的 adoption grant 是测试替身，ABANDON 的授权由真实 AG-2 写入
  并被 LR-2 重新验证。
- 正控：ACTIVE→PAUSED→真实 AG0 候选→AG1 合法集只有 `DEFER/ABANDON`→真实 validator→
  真实 AG-2 采纳 `APPLIED`→活动 `ABANDONED`、前台 `None`、未决 Action 仍是 `PROPOSED`。
- 负控：ATOMIC 无 ABANDON；capacity `NOT_NOW` 仍给 ABANDON；别的活动候选被 refresh /
  validator 拒；stale revision 被 AG-2 拒；无 owner grant 被 LR-2 拒；terminal 不复活、
  重放不产生第二次转换；WAITING 不发射候选；投影不生成 eligibility/condition/agenda。
- 相关旧回归：`tests.test_day_life_decision_legality` + `tests.test_paused_eligible_alignment`
  共 26 例通过。
- 未验证：真实模型自然放弃、遗留实例、Commitment/S08、G1 任一场景、24h。
