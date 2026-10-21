# v0.4 一天生活 · A 包（合法决策）结果

只做**法律一致性**：AG-1 不再把 owner 一定会拒的决策摆到模型面前，并且把"退出当前活动"
从执行资源里解耦。不做轮数提升、不做 no_change 台账、不做 NO_STEP 接纳分类、不改运行时长证据 ——
那些是 B / R 的写集。

写集（本轮实际改动，其余一律未动）：

| 文件 | 改动 |
|---|---|
| `components/life/scripts/agency_decision_ag1.py` | 修改（`+79 / -9`） |
| `tests/test_day_life_decision_legality.py` | 新增（10 个 exact 用例） |
| `.agents/notes/implemented/architecture/2026-10-26-v04-day-life-decision-legality.md` | 新增（专属决策笔记） |
| `docs/engineering/v04-day-life-legality-result.md` | 新增（本文件） |

未动：`life_integration_ag2.py`、`activity_continuity.py`、`candidate_sources_ag0.py`、
`autonomous_life_cycle.py`、`autonomous_life_runner.py`、`autonomous_life_resident.py`、
`subject_private_materials.py`、`life_pause.py`、`life_request_budget.py`、phone/world/supply 各件。
没有新增 schema、版本号、字段、候选种类、owner、写入域或 `DecisionRecord` 字段。

## 1. 交付的接口事实

### 1.1 新的有来源事实键（纯消费侧）

```python
# agency_decision_ag1.py:2215
OBSERVED_FACT_CURRENT_STEP_UNAVAILABLE = "current_step_unavailable_true"

# agency_decision_ag1.py:2218-2234
def observed_fact_is_true(observed_facts: Optional[Mapping[str, Any]], fact_key: str) -> bool
```

* 通道是**既有的** `DecisionContextSnapshot.observed_facts`；该键已经由
  `CognitiveDecisionRequest.observed_fact_keys`（`:3843`，`legal_decision_enums` 在 `:3841`）原样带给模型，
  因此 prompt 投影没有改动。
* 判定严格：键名必须是 ASCII 且 strip 后完全等于该键，值必须**就是**布尔 `True`。
  `"true"` / `"True"` / `1` / `"yes"` 都不算（用例如第 9 条）。
* 语义边界：只撤"当前活动的 CONTINUE"。它不产生决策、不改容量快照、不改世界事实、不改别的候选的合法集合
  （用例如第 10 条逐项比对）。**该键的生产者不在本包**（B 的 step readiness）；B 未接线前 A 的行为与之前完全一致。

### 1.2 `DecisionOutputValidator.compute_legal_decisions_for_candidate`（`:2245-2363`）

占用判定只有一行，且**绑定具体字段**：

```python
# :2271
foreground_holds_occupancy = bool(cur_act) and cur_status != STATUS_WAITING
#   cur_act    = context_snapshot.current_activity_ref     (None = 前台引用已释放)
#   cur_status = context_snapshot.current_activity_status  (ACTIVE / PAUSED / WAITING / None)
```

即：**引用还在且不是 WAITING** 就算被占用（PAUSED **不**释放引用 —— `activity_continuity.py:2290-2293`
pause 之后 `foreground_activity_ref` 仍指向那个 PAUSED 活动）。WAITING 是唯一"两个 owner 都放行
background waiting"的状态（AG-2 `:3093-3096` START / `:3387-3390` RESUME 传
`allow_background_waiting=True`；LR-2 `:1911-1922`、LR-4 `:2144-2157` 据此放行）。

| 决策 | 现在的条件（绑定字段） | 变化 |
|---|---|---|
| START | 候选种类可 START **且** `not foreground_holds_occupancy` **且** 非 ATOMIC **且** `exec_now` 允许 | 新增"前台引用被占用 → 不给"（含 PAUSED，不限 ACTIVE） |
| CONTINUE | 候选就是当前 ACTIVE 前台活动 **且** 无 `current_step_unavailable_true` **且** `exec_now` 允许 | 新增"无步骤事实 → 不给"；执行门保留 |
| PAUSE | 候选就是当前 ACTIVE 前台活动 **且** 非 ATOMIC | **移出**执行门 |
| ABANDON（`CURRENT_ACTIVITY` 分支） | 同上 | **移出**执行门 |
| ABANDON（`RESUME_ELIGIBLE` 分支） | `candidate.activity_ref == cur_act`（即目标就是当前前台活动）**且** 非 ATOMIC | 新增"非前台活动 → 不给"（LR-2 `:2158-2175` 必拒） |
| WAIT | 候选就是当前 ACTIVE 前台活动 **且** 有 `known_waiting_basis_ref` **且** `exec_now` 允许 | 未变 |
| RESUME | `RESUME_ELIGIBLE` + OPEN eligibility + `not (foreground_holds_occupancy and candidate.activity_ref != cur_act)` + 非 ATOMIC + `exec_now` 允许 | 新增"另一个活动的 RESUME → 不给"（含 PAUSED，避免 AG-2 先 pause 再失败） |
| DEFER / NO_ACTION | 恒合法（NO_ACTION 是全局结果，不在逐候选集合里） | 未变 |

ATOMIC 一刻也没放宽：`START` / `RESUME` / `PAUSE` / `ABANDON` 在 ATOMIC 下全部不给（用例第 6 条）。

### 1.2b 精确绑定的字段（没有新字段）

| 用途 | 字段（owner 不动） |
|---|---|
| 前台引用 / 其状态 | `DecisionContextSnapshot.current_activity_ref`、`.current_activity_status` |
| ATOMIC 边界 | `DecisionContextSnapshot.interruptibility`（+ `.current_atomic_boundary_ref` 只做指纹） |
| 无步骤事实 | `DecisionContextSnapshot.observed_facts["current_step_unavailable_true"] is True` |
| WAIT 依据 | `DecisionContextSnapshot.known_waiting_basis_ref` |
| 执行门（仅 START/CONTINUE/RESUME） | `AgencyCapacitySnapshot.candidate_executability[candidate_id]["executability"]` |
| 逐候选合法性 | `CandidateRecord.candidate_id` / `.candidate_kind` / `.source_kind` / `.activity_ref` / `.availability_status` / `.resume_eligibility_ref` / `.goal_step_ref` |

### 1.3 对齐的**真实**守卫（不是猜的）

| 事实 | 证据 |
|---|---|
| LR-2 拒"前台开着时 START" | `activity_continuity.py:1911-1922`（`foreground_conflict: cannot START while foreground_activity_ref=...`） |
| AG-2 采纳期拒"ACTIVE 前台时 START" | `life_integration_ag2.py:2637-2641`（`CANNOT_START_WHILE_FOREGROUND_ACTIVE`） |
| START 总是建**新** Activity | `life_integration_ag2.py:3098-3163`（新 `actv_...`，`start_activity`）；`allow_background_waiting` 只在前台是 WAITING 时为真（`:3093-3096`） |
| LR-4 拒"另一个活动的 RESUME" | `activity_continuity.py:2144-2157`；只有前台是 WAITING（或 `allow_background_waiting`）才放行 |
| AG-2 的 RESUME 会先偷偷 pause ACTIVE 前台，但仍然过不了 LR-4 | `life_integration_ag2.py:3391-3415`（pre-pause，`allow_bg_wait=False`）+ `activity_continuity.py:2290-2293`（pause 后 `foreground_activity_ref` 仍指向那个 PAUSED 活动） |
| PAUSE / ABANDON 的合法对象是当前前台活动 | `life_integration_ag2.py:3241-3251`（PAUSE 必须有前台）、`:3454-3456`（ABANDON 目标 = 决策目标或前台）；LR-2 `activity_continuity.py:2158-2175`（非前台活动的 ABANDON 被拒） |

`PAUSED` / `WAITING` / 无前台是**不同**的 owner 状态：判据是"引用还在不在 + 是不是 WAITING"，
不是"是不是 ACTIVE"。

## 2. 测试与退出码

运行环境：WSL `python3 3.12.3`，`PYTHONDONTWRITEBYTECODE=1`，只跑本类 `__dict__` 里定义的 exact IDs
（不 discover、不 `loadTestsFromTestCase`、不继承父类用例）。日志在 scratch：

| 组 | exact IDs | 结果 | 日志 |
|---|---|---|---|
| 新增 `tests/test_day_life_decision_legality.py::DayLifeDecisionLegalityTest` | 10 | **10 PASS / 0 FAIL，EXIT=0** | `a2-gen1-new-day-life-legality.log` |
| 直接受影响 seam `tests/test_life_situation_view.py::Ag1Seam` | 4 | **4 PASS / 0 FAIL，EXIT=0** | `a2-gen1-affected-ag1seam.log` |
| 受影响旧链 `test_one_day_life_w05r.LegalLifecycleTest` ×2 + `DecisionContractTest` ×1 + `test_life_v1_restart.RealLifeRestartTest` ×1 | 4 | **未能运行（导入期 SyntaxError，见 §3.1，未重跑）** | `a2-gen1-affected-day-life-legality.log`（失败证据保留，未重试刷绿） |
| **窄补后新增** `DayLifeOccupiedForegroundTest`（占用反例 4 条，只跑这 4 条，未重跑上面两组） | 4 | **4 PASS / 0 FAIL，EXIT=0** | `a3-gen2-occupied-foreground.log` |

新增的 10 个 exact ID（`DayLifeDecisionLegalityTest`，generation-1 全跑）：

1. `test_start_of_another_activity_is_withheld_while_the_foreground_is_active`
2. `test_start_is_still_offered_when_nothing_is_in_the_foreground`
3. `test_a_second_resume_is_withheld_while_the_foreground_is_active`
4. `test_pause_and_abandon_survive_a_blocked_budget`
5. `test_continue_pause_and_abandon_are_offered_when_capacity_is_available`
6. `test_the_atomic_boundary_still_withholds_pause_and_abandon`
7. `test_the_current_step_unavailable_fact_withdraws_only_the_current_continue`
8. `test_the_fact_with_a_false_value_leaves_the_continue_alone`
9. `test_a_truthy_looking_value_is_not_the_fact`
10. `test_the_fact_grants_no_other_decision`

窄补新增的 4 个 exact ID（`DayLifeOccupiedForegroundTest`，generation-2 只跑这 4 条）：

1. `test_start_is_withheld_while_a_paused_foreground_still_holds_the_reference`
2. `test_a_second_resume_is_withheld_while_a_paused_foreground_holds_the_reference`
3. `test_another_activitys_eligible_candidate_offers_no_abandon`
4. `test_start_is_offered_with_no_foreground_and_with_a_waiting_foreground`

用例全部用真实 `CandidateRecord` / `CandidateSet` / `DecisionContextSnapshot` / `CapacityEvaluator` /
`DecisionOutputValidator`，无 store、无 lease、无模型、无网络、无时钟依赖；没有 `TemporaryDirectory`、
没有清理、没有 `unlink`。

## 3. 具体限制与具名阻塞（不粉饰）

### 3.1 首轮半写导入失败与最终定点结果

`test_one_day_life_w05r` / `test_life_v1_restart` 在**导入期**就失败，原因是另一个 worker 正在写的
`components/life/scripts/life_runtime_coverage.py:279`：

```
SyntaxError: ':' expected after dictionary key
    complete = bool(rows) and not unknown_entries and unknown == 0 and all(
```

上述是首轮半写模块的导入期失败，红日志保留。R停写后父级 `day-life-final-exact-gen1.log` 已实际跑到四个exact ID：非法第二前台START拒绝、真实到达释放前台、loopback尊重legal enums三条通过；旧重启测试执行后红在 `test_life_v1_restart.py:388` 的过时“ACTIVE仍有START-legal候选”断言，非当前导入错误。测试负控合同收尾被DeepSeek 503/502阻断，未改测试或恢复非法START，剩余仅该ID待按现行矩阵对齐。父级另有gen5两独立进程真实owner有限链8项检查通过，不替代这条红测试。

`tests/test_one_day_life_w05r.py` 本来允许AG1或AG2两个精确拒绝点，父级定点实测已证明收紧后仍拒绝且owner不变。

### 3.2 剩余限制（本轮窄补后仍**不修**的，逐条具名）

1. **`WAITING` + 已释放前台的 ABANDON 被撤得过窄（under-offer，不是必拒）。** LR-2
   `activity_continuity.py:2166-2172` 允许 ABANDON 一个 `WAITING` 且 `foreground_released is True`
   的活动（那种情况下 `foreground_activity_ref` 已经是 `None`）。AG-1 的
   `DecisionContextSnapshot` **不带** `foreground_released` 这个事实，所以在
   `current_activity_ref is None` 时无法区分"它释放了"与"它从没占前台"，于是不给这条 ABANDON。
   方向是安全的（少给一条合法退出，绝不多给必拒）；要补齐需要新的 owner 事实/字段，本包不允许加字段。
2. **`current_step_unavailable_true` 的生产者不在本包。** A 只消费；B 接线前该键不存在，
   行为与改动前逐字节一致。
3. **决策请求的预算准入没有变。** PAUSE/ABANDON 解耦的是"能不能执行"，不是"能不能问模型"：
   请求账本照旧在 `agency_cognition_adapter.py` 与 `life_request_budget.py` 把关，本包没有绕过。
4. **WAITING 前台的 START/RESUME 依赖 AG-2 传 `allow_background_waiting=True`。**
   已核对：START `life_integration_ag2.py:3093-3096`、RESUME `:3387-3390` 都按"前台是 WAITING"传 True。
   若哪天 AG-2 不再传，AG-1 会多给一条（owner 仍会拒），方向仍是"owner 说最后一句"。

### 3.3 明确没做的事

* 未跑旧整库/全树回归、未 `py_compile`、未做全树 SHA、未 `git diff --check`（结构门父级最终一次）。
* 未启模型、未花产品模型额度、未读他处密钥、未改全局或实例 provider/配置。
* 未 commit / push / tag / package / deploy；未回滚任何钱包/room/PDI/作品历史。
* 未删除任何文件或 fixture（无物理删除、无 `TemporaryDirectory` 清理、无 no-op `unlink`）。
* 未递归委派。

## 4. 对父级一天链的意义（本包能证明 / 不能证明）

* **能证明**：只要前台引用还被占着（ACTIVE **或** PAUSED），模型的合法集合里就没有必被拒的
  START / 另一个活动的 RESUME / 非前台活动的 ABANDON；引用为 None 或指向 WAITING 活动时这些命令
  照旧提供（与两个 owner 的 `allow_background_waiting` 一致）。预算耗尽时她仍能合法选 PAUSE/ABANDON
  收尾；ATOMIC 与 CONTINUE 执行门没有被放宽；一条有来源的"当前活动没步骤"事实只撤 CONTINUE。
* **不能证明**：一天真的连续跑通。本包没有任何 24h 运行、没有真实 provider 调用，
  也不改变 runner 的轮数、NO_STEP 接纳分类、no_change 抑制或运行时长证据（B/R 的写集）。
  "已能正常活一天"仍需真实 24h 授权与运行证据，不能由本包改口。
