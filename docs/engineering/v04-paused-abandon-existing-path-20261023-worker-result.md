# PAUSED 自主放弃既有出口接通 · worker 结果（2026-10-23）

状态：施工完成，离线机制链通过；0 产品调用；未启动/修改主实例。

## 1. 结论先行

- PAUSED 的当前前台活动现在重新进入 AG-0 候选，AG-1 对它只给 `ABANDON`（+既有
  `DEFER`），真实 AG-2 `DecisionAdoptionCoordinator.adopt_decision` 能采纳，LR-2
  落到 `ABANDONED` 并释放前台。全程复用既有 `DECISION_ABANDON` 词表、AG-2 预授权、
  LR-2 admission/CAS/幂等与 experience handoff，没有新 candidate kind / schema / owner。
- 这不是「新增放弃能力」：LR-2 `abandon_activity` 早已允许 `PAUSED`
  （`activity_continuity.py:2548`），AG-2 的 ABANDON 分支也从不按状态拒绝
  （`life_integration_ag2.py:3652-3696`）。缺的只是 AG-0 投影与 AG-1 合法集两处窄接缝。
- 不生成 eligibility / condition / agenda，不自动 ABANDON，不给 CONTINUE / WAIT /
  PAUSE / RESUME / START；未解遗留实例，G1 未通过。

## 2. 改动文件（写集内 5 个）

| 文件 | 改动 |
| --- | --- |
| `components/life/scripts/candidate_sources_ag0.py` | `CurrentActivityCandidateSource.validate_source` 放行当前前台 `PAUSED`；`refresh` 在 `ACTIVE` 或 `PAUSED` 时保持 `OPEN`；docstring 说明投影不授予执行。仅此 source，不改其它 source。 |
| `components/life/scripts/agency_decision_ag1.py` | ① `HardConstraintEvaluator` 对 `SOURCE_CURRENT_ACTIVITY` 增加「live PAUSED + 候选就是快照当前前台 + 快照也说 PAUSED」的窄例外，revision 检查保留；② `compute_legal_decisions_for_candidate` 新增 PAUSED 分支，只追加 `ABANDON`（ATOMIC 除外，不看 `exec_now`）；③ `validate_decision_output` 的 ABANDON 分支在 PAUSED 时要求目标必须是当前前台。 |
| `tests/test_paused_abandon_existing_path.py` | 新增 11 例隔离机制链与正负控。 |
| `.agents/notes/implemented/bug-fix/2026-10-23-paused-abandon-existing-path.md` | 新决策笔记，互链 B1 与 day-life legal-set 两篇既有笔记。 |
| `docs/engineering/v04-paused-abandon-existing-path-20261023-worker-result.md` | 本报告。 |

没有改 AG-2 / LR-2 / LR-4 / cycle / assembly / resident / 存储格式 / prompt 字段；
保留所有既有 dirty，未 stage / commit / push。

## 3. 设计依据（具名）

- `原文分章/SRC-0719.md:12-13`：`READY / ACTIVE / WAITING_EXTERNAL / BLOCKED /
  PAUSED → COMPLETED / FAILED / CANCELLED / ABANDONED（相应 owner 判定）`。
- `SRC-0719.md:18`：放弃等待时 Activity 可 ABANDONED，并显式保留 unresolved Action 引用。
- `SRC-0719.md:32-38 Q10`：`ActivityAbandoned(activity_ref, commitment_binding,
  unresolved_actions, reason)` → S08 assessment obligation；不能仅凭放弃把 Commitment
  置 RELEASED。
- `SRC-0007.md:16`：`NO_ACTION 是合法结果；等待、暂停、放弃也是合法生活选择 /
  transition intent`。
- 既有 owner 事实：LR-2 `abandon_activity` 允许 `(ACTIVE, PAUSED, WAITING)`
  （`activity_continuity.py:2548`）；terminal 释放前台（`:2284-2286`）；AG-2 ABANDON
  分支（`life_integration_ag2.py:3652-3696`）不按状态拒绝。

## 4. 实现要点

### AG-0 投影

`PAUSED` 不是「新的可执行状态」，只是「这仍是当前 Activity」。候选仍是
`CANDIDATE_KIND_CONTINUE_CURRENT` / `SOURCE_CURRENT_ACTIVITY`，携带真实
`activity_ref`、`source_revision`、前台 provenance。`refresh` 仍要求：
活动存在、非 terminal、仍是前台、revision 未推进；只是不再把 `PAUSED` 判成
`ACTIVITY_NO_LONGER_ACTIVE_PAUSED`。`WAITING / INTERRUPTED / terminal` 一律不发射。

### AG-1 硬约束

live 检查的例外严格绑定三个条件，避免「所有非 terminal 都放行」：

```python
paused_foreground_self = (
    act_status == STATUS_PAUSED
    and cand.activity_ref == context_snapshot.current_activity_ref
    and context_snapshot.current_activity_status == STATUS_PAUSED
)
```

revision 不一致仍拒；其它非 ACTIVE 状态仍拒。

### AG-1 合法集

PAUSED 分支在既有 CONTINUE 分支之后，只追加 `ABANDON`：

```python
if (
    candidate.candidate_kind == CANDIDATE_KIND_CONTINUE_CURRENT
    and candidate.source_kind == SOURCE_CURRENT_ACTIVITY
    and candidate.activity_ref == cur_act
    and cur_status == STATUS_PAUSED
):
    if intr != INTERRUPTIBILITY_ATOMIC:
        legal.append(DECISION_ABANDON)
```

`DEFER` 是函数入口既有项。没有 `CONTINUE / WAIT / PAUSE / RESUME / START`；
`exec_now` 不参与，保持既有「暂停/放弃与预算解耦」规则。

### AG-1 validator

ABANDON 字段校验原来只要求选中候选有 `activity_ref`；现在 PAUSED 时额外要求
`sel_cand.activity_ref == cur_act`，防止手改候选或指向别的 Activity。

### 未改 AG-2 的验证

测试实际调用了真实 `DecisionAdoptionCoordinator.adopt_decision`：
`_issue_precommit_authorization` 写 `DECISION_ADOPTION_PRECOMMIT_AUTHORIZED` →
真实 `ActivityCommandService.abandon_activity` → 真实 journal/receipt →
`_validate_activity_commit_receipt` → `DECISION_ADOPTED`。adoption reader 先读真实
`IntegrationStore`，所以 LR-2 的 admission issuer 重新验证了 AG-2 刚写的
command-scoped grant（`authorized_mutation_kind = ABANDON`）。

## 5. 正负控（11 例）

| 用例 | 覆盖 |
| --- | --- |
| `test_paused_projection_reaches_real_ag2_adoption_and_releases_foreground` | 正控全链：ACTIVE→PAUSED→真实 AG0 候选→Layer1 ALLOWED→合法集恰好 `DEFER/ABANDON`→真实 validator→真实 AG-2 `APPLIED`→`ABANDONED`、前台 `None`、无 completion evidence、journal 带 decision/adoption refs。 |
| `test_abandon_keeps_unresolved_action_and_does_not_mark_it_settled` | 预置真实 AR-0 `PROPOSED` Action；ABANDON 后仍 `PROPOSED`、revision 与 `activity_ref` 不变，不伪造完成。 |
| `test_atomic_foreground_withholds_abandon_and_ag2_refuses_it` | ATOMIC：合法集只有 `DEFER`；真实 validator 抛 `ATOMIC_VIOLATION`。 |
| `test_capacity_unavailable_does_not_remove_the_abandon_exit` | `EXEC_NOT_NOW` 仍含 ABANDON，真实 validator 接受；同时不含 CONTINUE。 |
| `test_active_foreground_keeps_continue_pause_and_abandon` | ACTIVE 旧行为不变：含 `CONTINUE/PAUSE/ABANDON`，不含 `RESUME/START`。 |
| `test_waiting_foreground_is_not_projected_as_current_activity` | WAITING 仍不发候选。 |
| `test_terminal_activity_is_not_reprieved_and_cannot_be_abandoned_twice` | ABANDON 后不再投影；LR-2 再放弃抛 `ActivityTransitionError`；AG-2 重放 `idempotent_replay`，revision 不变。 |
| `test_paused_candidate_bound_to_another_activity_is_refused` | 指向别的活动：AG-0 refresh 非 OPEN；validator 拒绝。 |
| `test_stale_candidate_revision_is_refused_by_real_ag2` | 候选物化后 PAUSED→WAITING 推进 revision；AG-2 返回 `REJECTED_STALE` 且原因是 `SELECTED_CANDIDATE_STALE`。 |
| `test_lr2_refuses_abandon_without_an_owner_grant` | 无 Decision/Adoption 授权时 LR-2 admission 直接拒（`WriterCapabilityError`），活动仍 PAUSED。 |
| `test_projection_does_not_mint_eligibilities_conditions_or_agenda_items` | PAUSED 投影后 LR-4 三个 store 为空，不生成资格/条件/议程。 |

替身边界：模型 Decision 内容是脚本值；准备阶段 START/PAUSE 的 adoption grant 预置
（与 `test_paused_eligible_alignment` 相同做法）；interruption 端口是 IMMEDIATE/ATOMIC
窄替身。ABANDON 的授权、校验、终态全部真实。

## 6. 命令与退出码

```text
$ PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_paused_abandon_existing_path
Ran 11 tests in 0.778s
OK                                                        # exit 0

$ ... -m unittest tests.test_day_life_decision_legality tests.test_paused_eligible_alignment
Ran 26 tests in 0.450s
OK                                                        # exit 0（相关旧回归自检，非任务书验收）
```

## 7. 具名缺口 / 未验证项

1. **Q10 / S08 assessment 未接线。** 现有 Activity 记录没有 `commitment_binding`，
   AG-2 ABANDON 后只发 experience handoff，没有 S08 obligation。本单不新增字段或后果
   合同，因此只对「无承诺绑定」的活动成立。若要让带承诺的活动放弃仍守 Q10，需另开工单
   增加承诺绑定与 S08 评估，属于写集外契约扩张。
2. **unresolved Action 无法绑定同一 Activity。** AR-0 `ActionProposal` 要求
   `activity_ref` 以 `actv_` 开头（`action_reality_ledger.py:624-626`），AG/LR-2 的
   canonical refs 用 `actv:<id>`。测试只能验证一条已存在 PROPOSED Action 不被放弃动作
   结算/改写。两套词表本单都不扩。
3. **自然可达性未证。** 不声称真实模型会自然选择 ABANDON；不跑 resident/主实例。
4. **遗留实例未解。** 现场 `actv_91acbd742551228b` 仍是 PAUSED，本单不碰。
5. **G1 未通过。** 本单只接通一个既有出口，不是端到端场景。
6. 未跑全库、HTTP/browser、24h、产品模型。

## 8. 纪律

- 写集内 5 文件；未越界改 AG-2/LR-2/LR-4/cycle。
- 0 产品模型请求；本功能 cap 5 已用 1 余 4；共同历史仍 14/96 余 82，未刷新。
- 未运行/修改 `/root/nyairo-day-20261022`，未建实例，未 stage/commit/push。
- 每例使用独立 `tempfile.mkdtemp(prefix="paused-abandon-exit-")` 根，保留不清理。
