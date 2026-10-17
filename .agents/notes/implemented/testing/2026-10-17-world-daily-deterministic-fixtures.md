# Agent Note: 世界/每日三条用例的确定性夹具（时钟与候选合法性）

Status: implemented

## Problem

同一份产品树上，三条用例的红绿不取决于被测行为，而取决于跑它的那一天、那一刻和候选表顺序：

- `tests/test_world_daily_opportunities.py::DailyNeighborhoodTest::test_closed_door_is_allowed_in_timed_travel_too`
  断言"在途时位置仍是街口"。它读位置的辅助函数调 `world_living_skeleton.read_move_state(now=None)`，
  而该函数在 travel 打开时会先用**真实墙钟**结算到期行程。夹具注入的是 `2026-10-17 13:00 JST`
  （`04:00Z`），咖啡店段 `expected_arrival_at = 04:05:30Z`；真实 UTC 一越过这个点，尚未到达的行程就被
  结算成到达，位置读成 `cafe`，断言失败——尽管 owner 自己的 `world_state.json` 里仍是 `street_corner`。
- `tests/test_one_day_life_w05r.py::RequestBudgetTest::test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset`
  用固定字符串 `"2026-10-17"` patch `life_request_budget._utc_day` 来模拟跨日。而预算是
  `_active_day = max(stored_day, _utc_day())`，`reserve(now=…)` 的 `now` 只进 token 时间戳。真实 UTC 日期
  恰好等于被写死的那一天时，patch 是恒等变换，"新的一天"分支不可达，用例只剩当天的上限拒绝。
- `tests/test_life_v1_restart.py::RealLifeRestartTest::test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`
  的负控换回 `StubCognition("artifact")`，它按"非世界候选里的第一个"强发 `START`。窗口里同时有
  `CONSIDER_PERSONAL_OPPORTUNITY`（关于工作产物，legal 含 `START`）与 `CONTINUE_CURRENT`
  （legal 只含 `DEFER/CONTINUE/PAUSE/ABANDON`）；候选 id 每次运行都不同、表顺序不稳定，一旦后者排在前面，
  AG-1 先以 `INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE` 拒掉，AG-2 的
  `CANNOT_START_WHILE_FOREGROUND_ACTIVE` 根本没被触发。负控于是时而 `ADOPTION_REJECTED`、时而 `DECISION_FAILED`。

三条都不是产品缺陷：runtime 该结算就结算、该拒就拒。若把夹具继续留在"靠真实时钟/靠候选顺序"上，任何人
任何时候跑出红灯都无法判断是产品退化还是鞋子里有沙子。

## Decision

三条用例的夹具都把**不确定性显式化**，断言一条不减：

- **W（计时旅程）**：测试自己的 `position()` 辅助透传 `now=`；用例内三处读取都用用例的时钟。产品不改：
  `realize_due_travel` 在 `instant < expected_arrival_at` 时只返回 `in_progress`/`ARRIVAL_NOT_DUE`，不写盘，
  所以显式时钟下的读取仍是纯读。出发 ≠ 到达、闭店可走到门口、`inside` 无 affordances、真实到达后才变
  `cafe` 四条断言原样保留。
- **W05R（预算跨日）**：整条用例自带时钟。只从预算自己的账本读一次 `initial_day`
  （`budget.view()["utc_day"]`，不写死日期），据此算出 `next_day` / `prev_day`，三段分别在这三个显式
  `_utc_day` 补丁下跑。跨日 reset、回拨不 reset、上限、history 持久化断言全部保留，并额外钉住"回拨也不
  返还额度"：回拨日再花一次即到上限且 `check()` 不允许。
- **D-final（重启负控）**：负控换成 `StartControlLoopback`，只从 `legal_decisions` 含 `START` 的候选里挑，
  并优先挑 `target_refs`/`source_ref` 命中原 artifact 的那个；挑不到就当场抛错并 dump 整窗候选，不静默降级。
  选中的候选（id/kind/legal/about_work/整窗）写进 worker 报告，父进程断言它 `legal` 含 `START`、
  `about_work` 为真、决策为 `START`。于是这条负控证明的是"AG-1 允许的 START 在同一窗口被 AG-2 拒"，
  而不是"AG-1 先拒了"。

判定口径：**测试只在自己的边界内制造确定性**——时钟由用例给，候选由合法性+引用挑，产品与 runtime 一行不改。
负控宁可在前提不成立时大声失败，也不许降级成另一种拒绝码被"或运算"接受。

## Alternatives considered

- **把 `read_move_state` 的隐式结算去掉（产品改）**：最干净，位置读取彻底变纯读，"在途"事实不可能被读动作
  改掉。否决：这是世界 owner 的既有语义（懒结算），旅行包之外还有别的读取方依赖"读一次就把到期行程落地"；
  本任务是测试确定性窄修复，动它等于重开一次产品决策，授权范围之外。
- **W 用例改断言 owner 原始状态（`read_world_state`）**：同样能避开真实墙钟，且不碰产品。否决：它会把断言
  从"生命看到的移动状态"退回到"磁盘上的原始 JSON"，恰好绕开被测的那个投影函数；负控价值下降。选显式时钟，
  保留同一函数路径。
- **抓取真实当日再决定是否 patch（"今天不是那天才 patch"）**：能过，但把"是否走到跨日分支"变成随机条件，
  用例在多数日子里只验到一半。否决：确定性要无条件成立，不许有条件跳过。
- **负控接受两种拒绝码（`ADOPTION_REJECTED` 或 `DECISION_FAILED`）**：最小改动，红灯立刻消失。否决：这正是
  把负控变弱——`DECISION_FAILED` 说明 AG-2 根本没参与，"同窗口 forced START 被采纳层拒绝"这一条事实就不再被
  任何用例守着。
- **负控固定按候选 id 排序取第一个**：稳定但不可解释——id 是哈希，稳定只是碰巧，换个产品改动次序又变。否决：
  按**合法性**挑选才是这条负控真正要的语义。

## Consequences

- **收益**：三条用例在任何 UTC 日期、任何哈希种子、任何候选顺序下给出同一结论；红灯重新等于"产品变了"。
  负控从"有时验到 AG-2"变成"每次都验到 AG-2"，且失败时会带着整窗候选，不用再猜。
- **代价与已知上限**：夹具开始依赖产品的不变量——① `realize_due_travel` 在 `now < expected_arrival_at` 时
  不写盘；② 预算当日始终由 `_active_day`（而非 `reserve(now=)`)决定；③ AG-0 会为已完成版本的作品继续提供
  START 合法候选。任一不变量被产品改掉，这三条用例会以"断言失败/当场抛错"的方式通知，而不是静默变绿。
  另：W05R 那条不再假设"初始日就是某个具体日期"，读盘失败或账本为空时会以真实行为失败，不会被 patch 掩盖。

## Verification

- `tests.test_world_daily_opportunities.DailyNeighborhoodTest.test_closed_door_is_allowed_in_timed_travel_too`
  与 `tests.test_one_day_life_w05r.RequestBudgetTest.test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset`
  精确 id 同跑：`PYTHONHASHSEED=7` → returncode 0（修复前 returncode 1，两处失败原文见
  [world-daily-test-determinism-result.md](../../../../docs/engineering/world-daily-test-determinism-result.md) §4）。
- `tests.test_life_v1_restart.RealLifeRestartTest.test_a_real_restart_keeps_run_identity_and_continues_the_same_activity`
  精确 id 单跑：`PYTHONHASHSEED=12345` 修复前 `DECISION_FAILED`（`INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`），
  修复后 `ADOPTION_REJECTED` / `REJECTED_STALE` / `CANNOT_START_WHILE_FOREGROUND_ACTIVE`，returncode 0；
  `PYTHONHASHSEED` 7 / 1 / 987654 同为 0。
- 夹具与选择策略的现行事实：`tests/test_world_daily_opportunities.py::DailyNeighborhoodTest.position`、
  `tests/test_one_day_life_w05r.py::RequestBudgetTest::test_the_utc_day_rolls_forward_and_a_clock_rollback_does_not_reset`、
  `tests/test_life_v1_restart.py` 里的 `StartControlLoopback`。跨进程 harness 自身的取舍见
  [cross-process life restart harness](2026-10-17-cross-process-life-restart-harness.md)；被观察的世界投影见
  [world daily opportunities](../architecture/2026-10-17-world-daily-opportunities.md)。
