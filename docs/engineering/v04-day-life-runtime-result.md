# v0.4 一天生活连续性 R 侧交付结果：真实在线时间与 wall 跨度分开

> 三层表述（不合并）：**代码与纯时钟测试已验证**（14 条新增 exact 用例：gen1 12 条全绿；gen2 只跑新增 2 条，`Ran 2 tests ... OK`，exit 0）；
> **未跑真实 24h**（本轮不启动常驻、不花模型额度、无 provider）；**未接 B 的 runner 透传**（`extra.owner_version_*` 目前不存在，故 `owner_confirmed_versions` 保持 0 并把 ACTED 计入 unknown）。
> 本轮不合格的说法：`已经正常活了一天`、`SOAK_VERIFIED_24H`、`owner 确认了 N 个版本`、`重叠记录也算连续覆盖`。

- 仓库：`D:\nyairo-rc1-body-backend`（HEAD `41e818f`，此前 0.4 dirty 成果全部保留，未 add/commit/reset）
- 施工单：`D:\NYAIRO-v0.4-一天生活连续性-施工单.md`（R 段，独占写集）
- 决策记录：`.agents/notes/implemented/feature/2026-10-26-v04-day-life-runtime-coverage.md`

---

## 1. 写集（未越界）

```
新增：components/life/scripts/life_runtime_coverage.py（纯模块，392 行）
改：  components/life/scripts/autonomous_life_resident.py（只加进程账本/覆盖率/计数口径，原链路未重构）
新增：tests/test_day_life_runtime_coverage.py（14 例：gen1 12 + gen2 2）
新增：docs/engineering/v04-day-life-runtime-result.md（本文件）
新增：.agents/notes/implemented/feature/2026-10-26-v04-day-life-runtime-coverage.md
```

未触碰：`autonomous_life_cycle.py`、`autonomous_life_runner.py`、`autonomous_life_assembly.py`、
`one_day_life.py`、`artifact_continuation_source.py`、`components/phone/**`、`components/world/**`、
`components/supply/**`、任何配置/实例/数据库/`web/**`。

## 2. 修掉的具体项（逐条对应施工单 R）

| # | 原问题 | 现在的行为 | 证据 |
|---|---|---|---|
| R1 | `summary().observed_seconds = now - soak_started_at`，把停机算成生活 | `observed_seconds` 保留为**兼容键**，但值改为 `covered_seconds`（各进程自己 `time.monotonic()` 测量之和）；新增 `wall_span_seconds` / `process_online_seconds` / `offline_gap_seconds` / `overlap_seconds` / `unknown_seconds` / `coverage_complete` / `continuous` / `time_ambiguity` / `wall_rollback` / `observed_seconds_basis` | `autonomous_life_resident.py:382-402`（`_coverage_fields`） |
| R2 | 跨进程/重启把 monotonic 混用，停机被补成生活 | 每个进程一行账本（`resident_coverage.json`），monotonic **只在本进程内**取值，永不跨进程相加、永不用于跨 gap；gap 只用 wall 时间戳 | `life_runtime_coverage.py:209-344`（`coverage()`）、`:103-168`（进程标识/记录） |
| R3 | 异常中断的最后未闭合区间被当全程在线 | 未闭合行（`closed=UNCLOSED`）只承认它最后一次 heartbeat 的测量，之后到下一次启动（或 now）**计入 `unknown_seconds`**，`coverage_complete=False`；下一次启动不"修复"它 | `life_runtime_coverage.py:247-260`；`_open_process_record` 注释 |
| R9 | 两个进程的 wall 记录重叠时被当"安全连续"（`OVERLAP_IGNORED`，gap 记 0，`continuous=True`） | 负 gap 记 `kind=OVERLAP` + `ambiguous=True` + 真实重叠秒数；顶层 `overlap_seconds` / `covered_exceeds_wall_span` / `time_ambiguity`；`continuous` 在有任何时间歧义时为 **False**；`wall_rollback` 仍只表示"跨度本身为负"，不冒充重叠；**真实 monotonic 秒数一分不减** | `life_runtime_coverage.py:261-276`、`:305-310` |
| R10 | 无 monotonic 且未闭合的旧行，`NO_MONOTONIC_MEASUREMENT`（start→下一进程 start）与 `UNCLOSED_INTERVAL`（heartbeat→下一进程 start）**同区间双算** | 每个洞只记一次：无测量部分只到**本行最后一次 heartbeat**，之后才是未闭合尾巴；`_record_unknown()` 让"条目"和"总秒数"出自同一次调用，零秒不记；两个 reason 覆盖洞的两个不同片段 | `life_runtime_coverage.py:191-206`、`:247-260` |
| R4 | 旧 summary 兼容但可能伪 24h | 无账本的旧 summary 只按 `unknown_seconds` 计入（`sources.legacy_summary=PRESENT_UNPROVEN`），`covered_seconds=0`，`soak_24h` 保持 `SOAK_RUNNING_24H` | `life_runtime_coverage.py:171-188`（`legacy_summary_seconds`）、`:279-282`（计入 unknown） |
| R5 | `run()` 用 wall deadline 但每次睡满 `poll_seconds`，可能超时 | 每次等待被裁到 `min(poll_seconds, deadline - now)`，`remaining<=0` 直接退出；返回值分开 `process_online_seconds`（monotonic）/`wall_seconds`（wall）/`deadline_seconds` | `autonomous_life_resident.py:837-877` |
| R6 | `_count` 把所有 `ACTED` 一律计成 `owner_confirmed_versions` | 只有 wake **指名**了 owner 版本才计入；缺证进 `owner_version_unknown`；`actions` 仍表示 ACTED 窗口数 | `autonomous_life_resident.py:782-796`、`_owner_version` `:804-825` |
| R7 | `_model_calls_detail` 无 ledger 时漏 `reading_response` | `content_generation_calls` = content writer + reading-response writer 的 `call_count` 之和；新增 `reading_response_calls` / `surfaces` / `reading_response_counted`；缺表面报 `None`（unknown）不是 0 | `autonomous_life_resident.py:965-1023` |
| R8 | ledger 存在但读不出时被当 0 | `total_requests=None` + `total_source=LEDGER_UNREADABLE` + `ledger_state/ledger_reason`；有 ledger 时 `total_source=PERSISTED_REQUEST_LEDGER`（该账本按请求准入计，含 decision/content/reading_response/retry） | 同上 |

## 3. 字段契约（给 B，尚未被 B 实现，故当前是 UNKNOWN）

resident 只能从 runner 的 wake 记录里读 owner 版本，今天 `extra` 只有 `{"budget": ...}`
（`autonomous_life_runner.py:342/371/470`）。resident 读的最小契约，逐字如下：

```python
wake["extra"] = {
    "owner_version_created": True,       # 必需：owner 真的创建了版本
    "owner_version_id": "<str>",         # 二选一：owner 版本标识
    "step_path": "<str>",                # 二选一：步骤路径（与 created 同时出现才认）
    # "owner_version_ref": "<str>"       # 别名，等价 owner_version_id
}
```

判定规则（`_owner_version`）：

- `owner_version_id`（或别名 `owner_version_ref`）为非空字符串 → 记一个 confirmed 版本，ref 进 `owner_version_refs`（最多 20 条）；
- 或 `owner_version_created is True` 且 `step_path` 为非空字符串 → 记 confirmed，ref 用 `step_path`；
- 其余（含只有 `outcome=="ACTED"`、只有 `action_id`）→ `owner_version_unknown += 1`，**不推断**。

**这是 B 的活**：runner 侧把 owner receipt 写进 `extra` 之前，`owner_confirmed_versions` 恒为 0 是正确结果，不是回归。

## 4. 兼容性

| 读者 | 影响 |
|---|---|
| `one_day_life.py` 的 `window.observed_seconds` | **不受影响**：它用 journal 自己算 coverage/downtime，不读 summary 的 `observed_seconds` |
| `one_day_life.py` 的 `activity.owner_confirmed_results` | 读 `counters.owner_confirmed_versions`，该计数变严格（只认有证据的版本）；报告可能变小，方向正确。`one_day_life.py` 不在本写集，未改 |
| `soak_24h` / `soak_7d` 取值 | 词表逐字未变（`SOAK_THRESHOLD_REACHED_24H` / `SOAK_RUNNING_24H` / `SOAK_RUNNING_7D` / `SOAK_PENDING_7D`），但输入从 wall 跨度改为 covered 秒且要求 `continuous`；**永远不会**输出 `SOAK_VERIFIED*` |
| 旧 `resident_summary.json` | 可读；`observed_seconds` 的历史 wall 值被当 unknown（不删历史、不改写） |
| 旧 `resident_journal.jsonl` | 可重放；老记录没有 `extra.owner_version_*`，因此重放后 ACTED 进 `owner_version_unknown`（这是对旧数据的诚实读法） |
| `_journal` / `tick` / `settle_journey` / `start` 返回结构 | 未改键；`start()` 返回体未加新键 |

## 5. 新增 exact 用例（14 条 = gen1 12 + gen2 2，纯时钟/纯 summary/两个进程标识，不等 24h、不真模型、不启动 resident）

`tests/test_day_life_runtime_coverage.py`

1. `test_two_processes_that_stopped_are_not_one_continuous_life` — covered 5400 ≠ wall 12600，gap 7200，`coverage_complete=True` 但 `continuous=False`
2. `test_a_crashed_process_tail_is_unknown_and_never_up_since_the_start` — 未闭合行尾部 3000 进 unknown
3. `test_a_wall_rollback_is_not_negative_life` — wall 回拨 → wall_span 0 + `wall_rollback=True`，covered 不变
4. `test_a_process_without_a_measurement_is_unknown_not_zero_health` — 无测量 → unknown 1200、covered 0
5. `test_the_threshold_labels_never_mint_a_verification` — 达标且连续才 REACHED；有洞/被停机打断都保持 RUNNING；永不 VERIFIED
6. `test_a_legacy_summary_span_is_unknown_not_coverage` — 90000 秒旧 wall 跨度 → unknown 90000、covered 0
7. `test_an_old_summary_cannot_claim_a_day` — 磁盘上的旧 summary（含 `SOAK_THRESHOLD_REACHED_24H`）读出后是 `SOAK_RUNNING_24H`
8. `test_the_summary_reports_proven_coverage_not_the_wall_span` — 两个进程：covered 160 / wall 5160 / gap 5000；运行中 `coverage_complete=False`，关闭后 True 且 `continuous=False`
9. `test_a_restart_recovers_the_first_process_identity_and_leaves_it_closed` — 重启后第一行 id/closed/online 原样，新行 id 不同
10. `test_the_run_stops_at_the_wall_deadline_instead_of_sleeping_a_full_poll` — poll 180 / duration 60：只睡 60 秒、`wall_seconds=60`
11. `test_an_acted_wake_without_an_owner_receipt_is_not_a_confirmed_version` — 无证据 ACTED → confirmed 0 / unknown 1；带 `step_path` → confirmed 1
12. `test_the_model_request_total_includes_the_reading_response_surface` — 无 ledger 3 = 2+1；有 ledger 用账本 11；账本读不出 → None；无阅读表面 → None
13. `test_an_overlap_is_named_time_ambiguity_and_never_continuity`（gen2 新增）— a 01:00–02:00 / b 00:30–01:30 各 3600：covered 7200 不减、wall 1800、`overlap_seconds=5400`、`gaps=[OVERLAP/ambiguous]`、`wall_rollback=False`（跨度本身为正，准确）、`covered_exceeds_wall_span=True`、`time_ambiguity=True`、`continuous=False`、`coverage_complete=True`、`soak_24h=SOAK_RUNNING_24H`
14. `test_an_unmeasured_unclosed_row_is_not_counted_twice`（gen2 新增）— 无测量且未闭合的旧行（start 0、heartbeat 1200）后接新进程（start 3600、online 600）：`unknown_seconds=3600`，条目 `[1200 NO_MONOTONIC_MEASUREMENT, 2400 UNCLOSED_INTERVAL]`，条目和恰为 3600（不是 4800/6000），`gaps=[]`、`offline_gap_seconds=0`、`time_ambiguity=False`

运行（每次日志文件名唯一，失败保留）：

```
# gen1：12 条（已绿，gen2 按指示未重跑）
PYTHONDONTWRITEBYTECODE=1 python -m unittest test_day_life_runtime_coverage -v
Ran 12 tests ... OK        # exit 0
# 日志：$env:PI_SCRATCH_DIR\run3-runtime-coverage-20261018-195522.log

# gen2：只跑新增这 2 条 exact IDs
PYTHONDONTWRITEBYTECODE=1 python -m unittest -v \
  test_day_life_runtime_coverage.CoverageArithmeticTest.test_an_overlap_is_named_time_ambiguity_and_never_continuity \
  test_day_life_runtime_coverage.CoverageArithmeticTest.test_an_unmeasured_unclosed_row_is_not_counted_twice
Ran 2 tests ... OK         # exit 0
# 日志（唯一，含同一 2 条 ID 的 confirm-run 段）：$env:PI_SCRATCH_DIR\run6-overlap-and-doublecount-20261018-200617.log
# 红证（父级只读 probe，旧行为 OVERLAP_IGNORED + continuous=True）：$env:PI_SCRATCH_DIR\day-life-coverage-parent-boundaries-gen1.log
```

## 5.1 gen2 修正（父级只读 probe 复现，逐字）

父级 `scratch/day-life-coverage-parent-boundaries-gen1.log` 复现：a `01:00–02:00` online 3600、b `00:30–01:30`
online 3600，`coverage([a, b])` 得到 `wall_span=1800`、`covered=7200`、`gaps=[OVERLAP_IGNORED]`、
`wall_rollback=False`、`continuous=True`、`coverage_complete=True`。两个记录不可能都对同一段 wall 时间成立，
而旧实现把这段"重叠"记成 0 秒 gap 并仍宣称连续——**不能作为连续阈值证据**。

修正后的语义（两个问题分开答，都不减真实秒数）：

| 字段 | 含义 | 重叠时 |
|---|---|---|
| `covered_seconds` | 各进程自己 monotonic 测量之和 | **7200 不变**（不因时钟分歧缩减真实测量） |
| `offline_gap_seconds` | 已测量停机 | 0（重叠不是停机） |
| `overlap_seconds` | 负 gap 的绝对量，具名时间歧义 | 5400 |
| `gaps[]` | 每个相邻边界一条 | `{"kind": "OVERLAP", "ambiguous": true, "seconds": 5400}` |
| `wall_rollback` | **仅**表示"跨度本身为负" | False（本例跨度 +1800，保持准确，不冒充重叠） |
| `covered_exceeds_wall_span` | 覆盖大于它发生的 wall 跨度 | True |
| `time_ambiguity` | `wall_rollback` 或 `overlap>0` 或覆盖>跨度 | True |
| `coverage_complete` | 每段是否都有交代（都关闭、都测量、无 unknown） | True（账目完整） |
| `continuous` | 无停机洞**且**无时间歧义 | **False** |
| `soak_24h` | 只在 `continuous` 时 REACHED | `SOAK_RUNNING_24H` |

第二个修正：无 monotonic 且未闭合的旧行 + 新进程，旧实现同时按 `following-start`（NO_MONOTONIC_MEASUREMENT）
和 `following-heartbeat`（UNCLOSED_INTERVAL）计同一段，`start 0 / heartbeat 1200 / 下一进程 start 3600`
会得到 1200+3600=4800 或 3600+2400 的双算。现在每个洞只记一次：无测量部分只到本行**最后一次 heartbeat**
（1200），之后才是未闭合尾巴（2400），两段相接不重叠，`unknown_seconds=3600`，条目和恒等于总秒数。
`_record_unknown()` 让"条目"和"总数"出自同一次调用，零秒不记，所以既不会漏也不会重复。

## 6. 剩余限制（具名，不假装达标）

1. **没有真实 24h 证据**：本轮未启动常驻、未配置 provider、未花额度。`SOAK_VERIFIED_24H` 仍然只能由审阅者对着 journal + 进程账本 + owner 结果写，代码里没有这个标签。
2. **B已透传owner证据契约**：runner的 `extra` 现带 `owner_version_created/owner_version_id/step_path`，`b-gen3-new-quiet-runner.log` 精确透传测试通过；缺证仍None，不按ACTED推断。本轮父级连续链直接调用cycle，不是resident覆盖/计数长跑，因此不能从该链宣称resident已累计多少真实版本或在线小时。
3. **崩溃进程的覆盖上限是它最后一次 heartbeat**：tick 之间崩溃的那段算 unknown（不猜），所以覆盖率会略低于真实在线时间，方向安全。
4. **`journey_view()` 依赖 POSIX `fcntl`**：在 Windows 测试机上无法 import World journey 引擎，因此本轮 summary 用例对该视图做了实例级替身（`_without_journey_module`）。生产（Linux）不受影响；这是既有平台限制，不在本写集内修。
5. **`one_day_life.py` 的措辞未跟进**：它把 `counters.owner_confirmed_versions` 叫 "owner_confirmed_results"，计数变严格后该行可能更小。属父级/后续文档写集，本轮未改。
6. 本轮**没有**证明"一天生活连续性"本身（那是父级隔离实例链的验收目标）；R 只把"时间证据"从 wall 跨度改成可证明的在线覆盖，并把未知显式留成未知。
7. **重叠不是可容忍状态**：单写者 lease 下两个进程本不该同时持有同一份账本，所以 `OVERLAP` 意味着时钟跳变（NTP/挂起恢复）或记录损坏，必须查；系统只把它记成时间歧义、拒绝给它 `continuous`，不会把它当安全连续，也不会为了"看起来合理"去减真实 monotonic 秒数或把重叠当停机。
8. **`coverage_complete=True` 不等于"这一天可以验收"**：它只说每段都有交代；能否作为连续阈值证据由 `continuous` 与审阅者的 journal/owner 结果共同决定。重叠案例里前者为真、后者为假，这正是两个问题分开报的原因。
