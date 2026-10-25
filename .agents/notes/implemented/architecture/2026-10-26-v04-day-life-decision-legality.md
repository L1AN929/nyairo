# Agent Note: v0.4 一天生活 —— AG-1 只提供 owner 真的会接受的决策（单前台 + 合法退出 + 无步骤事实）

Status: implemented

## Problem

0.4 的目标是主体正常活一天，但真实连续运行链在 AG-1 这一层就先把路堵死了三处：

1. **必被拒的 START 照样进 prompt。** `DecisionOutputValidator.compute_legal_decisions_for_candidate`
   只按候选种类 / ATOMIC / `exec_now` 判 START 合法性，完全不看当前前台
   （原 `agency_decision_ag1.py:2237-2245`）。可 AG-2 在采纳期硬拒第二个前台
   （`life_integration_ag2.py:2637-2641` `CANNOT_START_WHILE_FOREGROUND_ACTIVE`），LR-2 更是任何
   前台开着就拒 START（`activity_continuity.py:1911-1922`）。于是作品 Activity 还 ACTIVE 时，
   阅读/世界候选的 `legal_decisions` 里仍然有 START，模型一选就被拒，一轮决策请求白烧，
   报告还写成技术阻塞而不是"她选了别的"。
2. **容量/预算耗尽时连退出都关了。** CONTINUE / PAUSE / WAIT / ABANDON 全都嵌在同一个
   `exec_now in {EXEC_NOW, EXEC_WITH_LIMITS, EXEC_UNKNOWN}` 守卫里（原 `:2248-2264`）。请求账本一拒，
   资源列变 `UNAVAILABLE`，每个候选都 `EXEC_NOT_NOW`（`:2036-2043`），合法集合只剩 DEFER。
   最需要搁置/收尾的时刻恰好不能搁置，Activity 永远开着。
3. **没有"这一步真的做不下去了"的事实通道。** 材料读尽 / 作品 no_change 之后，前台仍是 ACTIVE、
   CONTINUE 仍合法，但执行时没有任何步骤可跑。AG-1 需要一条**有来源、只描述当前活动**的事实
   来撤掉这一条 CONTINUE，同时不能把合法的 pause/abandon 一起撤掉。

约束：不改 schema / version / owner / 决策模型字段，不新增候选种类，不动 AG-0/AG-2/store/cycle/runner，
不把"所有前台状态"粗暴同义（ACTIVE 与 PAUSED/WAITING 是不同 owner 状态、不同真实守卫）。

## Decision

只改 `components/life/scripts/agency_decision_ag1.py`（79 插入 / 9 删除）：

1. **新增有来源事实键 `OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE = "current_step_unavailable_true"`**
   与纯判定 `observed_fact_is_true(observed_facts, fact_key)`（`:2207-2234`）。只有
   **ASCII 键名完全匹配**且值**就是布尔 `True`** 才算这条事实：`"true"`、`"True"`、`1`、`"yes"` 都不算。
   它走的是既有 `DecisionContextSnapshot.observed_facts` 通道 —— 该键已经由
   `observed_fact_keys`（`:3843`）原样带给模型，因此不需要任何新字段、不改 prompt 投影。
2. **前台引用被占用时不给 START**（`:2288-2300`）。START 在 AG-2 里总是 `start_activity` 建**新**
   Activity（`life_integration_ag2.py:3098-3163`），所以引用还在时任何 START 都是第二前台。判据不是
   "是不是 ACTIVE"，而是绑定两个字段的一行：
   `foreground_holds_occupancy = bool(current_activity_ref) and current_activity_status != STATUS_WAITING`
   （`:2271`）。LR-2 正是"引用还在就拒 START"（`activity_continuity.py:1911-1922`），只对 WAITING 前台
   配 `allow_background_waiting` 放行（AG-2 `:3093-3096` 就按这个传）。PAUSED **不**释放引用：
   pause 之后 `foreground_activity_ref` 仍指向那个 PAUSED 活动（`activity_continuity.py:2290-2293`）。
3. **另一个活动的 RESUME 在引用被占用时不给**（`:2330-2351`）。LR-4 在 `fg_id != act_id` 且前台不是
   WAITING 时抛 `foreground_conflict`（`activity_continuity.py:2144-2157`）。AG-2 的 RESUME 分支会先
   偷偷 pause 一个 ACTIVE 前台（`life_integration_ag2.py:3391-3415`），但 pause 之后前台仍指向那个
   PAUSED 活动，LR-4 照样拒 —— 那会留下"暂停了别人、命令仍失败"的真实副作用。所以只要
   `candidate.activity_ref != cur_act` 且引用被占用就整条不给；恢复前台自己（`fg_id == act_id`）时
   LR-4 跳过前台检查，照旧合法。
4. **PAUSE / ABANDON 与执行资源解耦，但身份不松**（`:2302-2328`、`:2352-2361`）。`CURRENT_ACTIVITY`
   分支：只在"这个候选就是当前 ACTIVE 前台活动"（身份 + 非终态 + `cur_status == ACTIVE`）且
   `interruptibility != ATOMIC` 时给 PAUSE/ABANDON，不再看 `exec_now`。`RESUME_ELIGIBLE` 分支的
   ABANDON 原来对"别人的活动"也给，但 LR-2 `:2158-2175` 对非前台活动的 ABANDON 同样必拒，因此现在
   只在 `candidate.activity_ref == cur_act`（目标就是当前前台）时才给。CONTINUE 仍留在执行门内
   （`not step_unavailable and exec_now in {...}`），WAIT 的既有执行门也原样保留。ATOMIC 一刻也不放宽：
   START/RESUME/PAUSE/ABANDON 全部不给。
5. **无步骤事实只撤当前 CONTINUE**（`:2309-2315`）。`step_unavailable` 为真时不给 CONTINUE，
   PAUSE / ABANDON / WAIT / DEFER 以及全局 NO_ACTION 一个不动；它不授权任何别的决策、不改容量快照、
   不改世界事实（测试里断言同一事实下别的候选合法集合逐项相等、`executability` 仍是 `EXEC_NOW`）。

没有新增 schema、字段、版本号、候选种类、owner 或写入域；没有动 AG-2 / AG-0 / store / cycle / runner /
resident / phone / world / supply。

## Alternatives considered

* **让 AG-2 的前台占用校验放宽，允许"切换"时静默 pause 前台。** 最强立论：模型想换事做是正常的，
  owner 直接替她收尾最省事。否决原因：那是**替她做选择**（正是 `19:9` 禁止的"脚本代替主体决定"），
  而且会绕过 ATOMIC/中断协议；单前台合同是承重墙，本包只做"不给必被拒的选项"。
* **在 `AgencyCapacitySnapshot.candidate_executability[cid]` 里加 `step_available` 字段。** 最强立论：
  容量快照本来就是"现在能不能执行"的地方，放这里模型一眼能看到。否决原因：那是给决策模型/快照
  加字段（本包明确不改 schema/字段），而且"没有步骤"不是容量事实 —— 容量是身体/设备/资源/隐私，
  步骤可用性是活动材料的真实状态。走既有 `observed_facts` 事实通道，零 schema 变更。
* **把 PAUSE/ABANDON 从 `CURRENT_ACTIVITY` 候选分支里整个搬出去，做成"只要有前台就永远合法"。**
  最强立论：退出应该无条件可用。否决原因：PAUSE/ABANDON 的合法对象是**当前前台活动本身**
  （AG-2 `3241-3251` PAUSE 必须有前台、`3454-3456` ABANDON 目标=决策目标或前台），
  脱离"这个候选就是它"的身份就变成"放弃别人的活动"，LR-2 `2158-2175` 也会拒。只解耦执行资源，不解耦身份。
* **对 PAUSED 前台也一起不给 START。** 最强立论：LR-2 `1911-1922` 对 PAUSED 前台同样拒 START，
  按"必被拒就不提供"的口径也该撤。否决原因：本包授权只到 ACTIVE 前台；而且在没有 OPEN resume
  eligibility 时，撤掉 START 会让模型只剩 DEFER，把一个 owner 层的僵局变成"她连试都不能试"。
  这条按具名残留上报（见下），等父级决定，不在本包擅自放宽。
* **在 validator 里也拒掉"无步骤事实下的 CONTINUE"。** 最强立论：不给就不该能选。否决原因：
  B 包的合同是"她仍可以选 CONTINUE，但结果必须具名 `NO_STEP_AVAILABLE`（blocked 而非 choice）"。
  若在 AG-1 把它判成非法输出，就会变成技术失败而不是"她选了一个做不到的步骤"，两者语义不同。
  AG-1 只负责"不提供"，接纳侧的具名分类归 B。

## Consequences

* 前台引用被占用（ACTIVE **或** PAUSED）时，模型的 `legal_decision_enums` 与逐候选 `legal_decisions` 里
  不再出现必被拒的 START / 另一个活动的 RESUME / 非前台活动的 ABANDON；引用为 None 或指向 WAITING
  活动时这些命令照旧提供（与两个 owner 的 `allow_background_waiting` 一致）。同样的输出现在也会被
  `validate_decision_output` 以 `INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE` 拒掉
  （`FAILURE_CLASS_ILLEGAL_DECISION_MATRIX`）。`tests/test_one_day_life_w05r.py:933-962` 早已把两个
  拒绝点都写成合法结果，合同没有被放宽。
* 预算/资源耗尽时，PAUSE 与 ABANDON 仍然提供给模型：她可以在花不起钱的时候合法搁置/收尾，
  CONTINUE 依旧受执行门约束（不会假装还能继续）。
* `current_step_unavailable_true` 一旦由 B 的 step readiness 放进 `observed_facts`，
  当前 CONTINUE 消失、合法退出与 DEFER/NO_ACTION 保留；该键为 `False` 或非布尔值时完全保持既有语义。
  在 B 接线之前，本包行为与之前逐字节相同（没有生产者 → 事实不存在 → 一切照旧）。
* 剩余限制（具名，本包**不修**）：
  1. `WAITING` + 已释放前台的活动按 LR-2 `2166-2172` 仍可 ABANDON，但 AG-1 的快照不带
     `foreground_released` 事实，`current_activity_ref is None` 时无法区分，于是这条 ABANDON 被
     **少给**（under-offer，不是必拒）。补齐需要新的 owner 事实/字段，本包不允许加字段。
  2. `current_step_unavailable_true` 的**生产者**不在本写集（B 的 step readiness）；A 只消费。
  3. 容量维度本身没有变：本包不新增容量维度，也不把 UNKNOWN 变 AVAILABLE。
  4. WAITING 前台的 START/RESUME 依赖 AG-2 传 `allow_background_waiting=True`
     （已核对 `life_integration_ag2.py:3093-3096`、`:3387-3390`）；若哪天不传，AG-1 会多给一条，
     owner 仍会拒。
* 代价：`compute_legal_decisions_for_candidate` 多两次小判定（一个布尔 + 一次 ≤N 键的 observed_facts
  扫描），零额外 owner 读、零模型调用。

## Verification

* `tests/test_day_life_decision_legality.py::DayLifeDecisionLegalityTest` 10 个 exact IDs 全 `PASS`，
  `EXIT=0`，日志 `scratch/a2-gen1-new-day-life-legality.log`（`SUMMARY ids=10 failures=0`）。
  用例按 `__dict__` 选，不 discover、不展开父类。
* 窄补（占用反例）`DayLifeOccupiedForegroundTest` 4 个 exact IDs 全 `PASS`，`EXIT=0`，日志
  `scratch/a3-gen2-occupied-foreground.log`（`SUMMARY ids=4 failures=0`）。按指示**只跑这 4 条**，
  没有重跑上面 10 条与 Ag1Seam 4 条。
* 直接受影响的既有 seam：`tests/test_life_situation_view.py::Ag1Seam` 4 个 exact IDs 全 `PASS`，
  `EXIT=0`，日志 `scratch/a2-gen1-affected-ag1seam.log`。
* 未能跑的受影响旧链：`test_one_day_life_w05r.LegalLifecycleTest` /
  `test_life_v1_restart.RealLifeRestartTest` 在导入期就被**另一个 worker 正在写**的
  `components/life/scripts/life_runtime_coverage.py:279` 语法错误挡住（`SyntaxError: ':' expected
  after dictionary key`，不在本写集）。失败日志保留在 `scratch/a2-gen1-affected-day-life-legality.log`，
  没有重试刷绿。
* 未跑旧整库/全树回归、未 `py_compile`、未做全树 SHA；`PYTHONDONTWRITEBYTECODE=1` 全程开启；
  没有删除任何 fixture（无 `TemporaryDirectory` 清理、无 no-op `unlink`）；未改配置/实例/密钥，
  未 commit/push/deploy。

## 后续规则（2026-10-24，只改 PAUSED 的槽位事实）

用户批准 PAUSE 释放执行槽之后，**新产生**的 PAUSED Activity 不再让
`foreground_activity_ref` 指向自己，因此 AG-1 在下一轮会正常提供 START 和另一活动的
RESUME；旧版本写下的 PAUSED 指针没有 `foreground_released` 标记，仍按本笔记的“占用”
规则处理，不会被读取路径迁移。`foreground_holds_occupancy` 的判据本身没有改：它读的是
前台引用，而新规则改的是 owner 在 PAUSE 时是否留下引用。ABANDON 的
`PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE` 安全门也原样保留。见
[PAUSE 释放执行槽](../feature/2026-10-24-paused-background-single-execution.md)。
