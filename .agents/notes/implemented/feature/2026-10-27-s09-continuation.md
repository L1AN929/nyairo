# Agent Note: S09 持续活动 —— 停、等、版本变化、回来与真正结束（F-103～F-108）

Status: implemented

## Problem

S09 的六个行为在设计里是一条链，但树上只有分散的机制：Activity 的 START/CONTINUE/WAIT/PAUSE/
RESUME/ABANDON、LR-2/LR-3/LR-4/LR-5、AR-0/AR-1 各自有测试，缺的是一次**同一活动**的
连续证据。核对时另外发现三处真实断点：

1. **暂停后的收尾候选供不出来。** `artifact_continuation_source._read_points` 为
   「PAUSED/WAITING 目标必须走 RESUME」跳过该作品，但 closeout 候选只在同一个 `points`
   循环里追加；于是 cycle 的 `_closeout_point` 认为「PAUSED 已释放前台，可以收尾」，
   源却永远不会发出这个候选。暂停的工作实际上无法结束。
2. **手机读面只有状态，没有回来入口。** `/today` 与 timeline 已如实显示
   ACTIVE/PAUSED/WAITING/ABANDONED/COMPLETED，但没有说明「为什么现在回不来」以及
   「owner 依据支持的下一个入口」。资格与条件都在 LR-4 自己的 store 里，读面没有投影。
3. **CANCEL 与 Activity FAILED/BLOCKED 没有生产路径。** LR-2 状态机有 `CANCELLED`，
   但没有 composition-root 的 CANCEL consequence capability；Activity 枚举也没有
   `FAILED`/`BLOCKED` 终态。把它们做成「看起来通过」需要新增权限/终态协议，属于停止门。

## Decision

**1. 一条同活动的实际链（`scripts/s09_continuation_demo.py`）。** 复用既有 owner，不新造
Activity/schema/Decision 词：

- 真实 START 作品 A → 真实版本（停点 = Activity `checkpoint_ref` + owner head）→
  writer `no_change` → 正式 WAIT 绑 owner head 依赖；
- WAITING 不占执行槽：另一件真实活动 B 在 A 等待时 START；只有 owner head 真变，
  `maintain_waiting` 才让 LR-4 发一个 OPEN；`NO_ACTION` 保持 WAITING，不自动恢复；
- 同根有序重启（真实 shutdown → drain → socket guard → 新 runtime/assembly）读回
  Activity/停点/条件/OPEN 资格；stale revision 的 RESUME 被 LR-2 CAS 拒，资格不消费；
  新鲜 RESUME 回到同一 Activity id，按 fresh head 写新版本，旧版本不重放；
- 并发 head 变化让旧 `expected_head` 被 owner 明确拒（`STEP_REFUSED`），新 Decision 在
  新 head 上继续；不静默 rebase、不换作品；
- `ATOMIC` 拒绝 PAUSE（AG-1 `ATOMIC_BOUNDARY_VIOLATION`），不 forced stop；
  `SAFE_BOUNDARY` 只登记 pending，Activity 保持 ACTIVE，`handle_boundary_reached` 才 PAUSED；
- 收尾 Activity 的 Action 先被并发 revision 拒（`FAILED`/`DEFINITE_PROVIDER_REJECTION`，
  Activity 仍 ACTIVE），再用受控丢答产生 AR-0 `UNKNOWN`；重启后仍 UNKNOWN，
  `reconcile_unknown_step` 用**原 owner operation** 对账并结算，LR-5 完成合同只有在
  真实归档回执下才把 Activity 置 COMPLETED（`completion_evidence_ref`），不产生第二次归档。

**2. 收尾供应的窄修（`artifact_continuation_source.py`）。** 当 closeout target 指向的
作品因为 PAUSED/WAITING 被 continuation 跳过时，单独读一次该 owner point，只追加
closeout 项，不追加 fresh START。`per_source_cap` 仍约束 continuation；closeout 是不同动作。

**3. 手机只读恢复投影（`components/phone/own_day_read.py`、`service.py`、`day_timeline.py`）。**
只读 LR-4 的 `resume_eligibility_state.json` 与 `resume_conditions_state.json`：

- OPEN（未过期）资格 → `RESUME_ELIGIBILITY_OPEN`，`next_legal_entry = RESUME`；
- PENDING 条件 → `RESUME_CONDITION_PENDING`，没有入口（等 owner 事件）；
- terminal → `TERMINAL_<status>`；ACTIVE → `ACTIVE`（CONTINUE 还取决于 step readiness，
  不在这里声称）；
- WAITING/PAUSED 无 OPEN/PENDING → `NO_OPEN_ELIGIBILITY`，不造 timer；
- store 读不到/损坏 → `ELIGIBILITY_UNREADABLE`，未知不等于没有。

## Alternatives considered

- **给 Activity 新增 FAILED/BLOCKED 物理终态。** 最强理由：Q8 原文就是这么写的，手机和
  报告都能直接显示「失败/阻塞」。否决：现有 LR-2/LR-5/AR-0 没有 FAILED 的 owner 命令与
  证据准则；新增终态协议是产品/契约决定，本组只把 Action/step 的 FAILED 当作证据，
  Activity 保持 ACTIVE/WAITING，并把「没有物理枚举」作为具名缺口交出。
- **为 CANCEL 在 composition root 直接 mint 一个 consequence capability。** 最强理由：
  一行工厂调用就能让外部撤销真实落成 CANCELLED，演示也完整。否决：那是新增权限；本单
  只能证明 owner-backed admission 会拒绝没有授权的 CANCEL，不能自行授予自己。
- **让 closeout 绕过 source 直接手造候选。** 最强理由：演示立刻能跑。否决：候选必须经
  AG-0 真实 source；手造候选等于跳过供给合同。修 source 的窄分支是同一合同内的漏接。
- **WAITING 时自动 RESUME / 条件满足自动恢复。** 最强理由：体验顺、少一次决策。否决：
  既有笔记已定案「满足只发资格，回不回来由新鲜 Decision 选」；本单不替她做决定。
- **在手机读面把没有资格的活动显示成「可恢复」或补一个默认计时器。** 否决：那是把
  「没有 owner 依据」包装成入口；读面只投影 owner 已有的事实。

## Consequences

- S09 的六项有了一条可复跑的同活动证据；`scripts/s09_continuation_demo.py` 直接打印每项
  CHECK，末尾以 `S09_CONTINUATION_DEMO_OK` 结束。
- 暂停但仍 ACTIVE 的作品现在能收到 closeout 候选，同时**不会**收到 fresh START；
  这是 fix，不是把「暂停」偷换成「可继续」。
- 手机 `/today`/timeline 的 Activity 行多一个只读 `recovery` 块；没有资格时明确
  `NO_OPEN_ELIGIBILITY`，读不到时 `ELIGIBILITY_UNREADABLE`。不新增写口、不新增资格。
- 具名缺口（本单不伪造）：Activity `FAILED`/`BLOCKED` 终态；生产 CANCEL authority
  capability。两者若要做，属于新增终态协议/权限，需要单独裁定。
- 边界不变：Decision/内容是脚本替身；0 产品模型、0 外网、0 新图片；不是 G1，不是 24h，
  不证明真实模型自然采用。World 未参与本链；所有效果在真实 Supply/Activity owners。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s09_continuation_demo.py`
  → 全部 CHECK PASS，`S09_CONTINUATION_DEMO_OK`，exit 0（真实 owner，两处同根重启）。
- `tests/test_s09_continuation.py` 6 例：OPEN→RESUME、PENDING 条件不恢复、无资格不造 timer、
  terminal/ACTIVE 区分、过期资格不算 OPEN、损坏 store 是未知不是没有。
- `tests/test_life_closeout_cycle_v04.py` 新增 1 例：PAUSED 作品仍有 wrap-up、没有 fresh
  continuation；全套 9 例通过。
- 直接相关回归：`tests.test_artifact_closeout_v04`、`tests.test_paused_background_single_execution`、
  `tests.test_paused_explicit_condition` 共 43 例通过；`tests.test_phone_today`、
  `tests.test_phone_day_timeline` 共 9 例通过。

相关决定：[一天生活核心连续性](../architecture/2026-10-26-v04-day-life-core-continuity.md)、
[PAUSE 释放执行槽](2026-10-24-paused-background-single-execution.md)、
[显式条件 PAUSE](2026-10-24-paused-explicit-resume-condition.md)、
[等待与收尾](2026-10-28-v04-wait-closeout-core.md)、
[身体断点与回来](2026-11-15-v04-life-pacing-breakpoint-and-resume.md)、
[PAUSED 资格对齐](../bug-fix/2026-10-23-paused-resume-eligibility-alignment.md)、
[PAUSED ABANDON 扣留](../bug-fix/2026-10-23-paused-abandon-withheld.md)。
