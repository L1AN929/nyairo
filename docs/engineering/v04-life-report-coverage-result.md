# v0.4 一天报告 R 侧交付结果：安静在线与「一天覆盖」统一到同一本账

基准：唯一树 `D:\nyairo-rc1-body-backend`，rc1 HEAD `41e818f`，全部既有 dirty 保留；本轮只写
R 的三份产品文件 + 一个新用例文件 + 本回执 + 一份 note，未 reset/clean/checkout，未动
MANIFEST/两个 move 脚本/发布物/原实例/真实模型。

## 0. 一句话

日报告不再从 resident journal 的首末瞬时「推断在线」：它读同 root 的持久账本
`resident_coverage.json`，把 wall / covered / offline / unknown / ambiguity 与「已闭合 vs 当前
tail」分开报出；没有账本（只有 journal 或只有旧 summary 墙钟跨度）就明确 **unverified，不能 PASS**；
到阈值不等于 `SOAK_VERIFIED`；零 owner 产出/零请求既不当「没活」，缺模型/被 blocked 也不称「选择休息」。

## 1. 写集（未越界）

| 文件 | 性质 | 改动 |
|---|---|---|
| `components/life/scripts/life_runtime_coverage.py` | 窄修（纯函数，只加字段） | `coverage()` 把 covered 秒数按「区间是否闭合」拆成 `closed_process_seconds` / `open_tail_seconds`（`:231`、`:253-256`、`:331-332`）；每进程投影补 `started_at` / `last_heartbeat_at`（`:343-345`）；模块 docstring 补一条（`:14-19`）。原有字段、语义、返回值一个没改。 |
| `components/life/scripts/autonomous_life_resident.py` | 窄增（只读方法） | 新增 `recorded_coverage(*, now=None)`（`:374-392`）：**从别的进程眼里**读账本，`current_process_id=None`，未闭合行的尾部留在 unknown，旧 summary 的墙钟跨度只在没有账本时进 unknown。`runtime_coverage()`（`:356-372`，本进程活体视图）一字未动。 |
| `components/life/scripts/one_day_life.py` | `build_report` 相关 | 常量 `COVERAGE_BASIS_*` / `QUIET_*`（`:136-152`）与 `import life_runtime_coverage`（`:71`）；新 `_journal_window`（`:1274`）、`_report_quiet`（`:1299`）、`_current_tail`（`:1375`）；`build_report` 重写（`:1416-1614`），新增 `now=` 注入时钟入参；返回体新增 `window.coverage_basis` / `measured` / `current_tail` / `soak` / `quiet`，旧键全部保留并加 basis。 |
| `tests/test_life_report_coverage_v04.py` | 新增 | 12 条精准用例。 |
| 本文件、`.agents/notes/implemented/feature/2026-10-27-v04-life-report-coverage.md` | 新增 | 回执与决策 note。 |

未动：`autonomous_life_cycle.py` / `autonomous_life_runner.py` / `autonomous_life_events.py` /
`autonomous_life_assembly.py` / `artifact_continuation_source.py` / `life_runtime_production.py` /
`life_artifact_port.py` / AG0/AG1/AG2/LR4/LR5 / `MANIFEST.json` / 两个 move 脚本 / WSL 侧任何东西。

## 2. 原缺陷，以及父级审计里两处过强的推论（已纠正）

**缺陷 1：报告用墙钟跨度冒充在线。** 旧 `build_report` 取
`resident_journal.jsonl` 里**第一条与最后一条记录**的瞬时当作 coverage，再把任何大于
`poll × 2.5` 的相邻间隔全部算成 downtime，`observed = coverage - downtime`（旧 `:1275-1287`）。
两个进程、一次崩溃、一次重启在这套算术里长得一样；墙钟也会被 NTP/休眠污染。这正是 R 前一批
`life_runtime_coverage` 已经消灭的口径，报告侧却还在用。

**缺陷 2：旧 summary 的假标签被直接透传。** 旧 `window.soak_label = summary["soak_24h"]`
（旧 `:1338`），于是一份只有墙钟 `observed_seconds=90000` 的旧记录，只要自己写着
`SOAK_THRESHOLD_REACHED_24H`，报告就照着抄。

**纠正 A（quiet 的稀疏不是 journal 造成的这一条过强）。** 审计 §1#1 说
「journal 对连续 quiet 只写第一条（`autonomous_life_runner.py:542-545`）」，因此报告首末/间隔会
把安静算成 gap。核实结果：那段 quiet 合并写的是 **runner 自己的 wake log**
`autonomous_wakes.jsonl`（`autonomous_life_runner.py:58,231,538-545`）；`build_report` 读的是
**resident 的** `resident_journal.jsonl`（`one_day_life.py:1248-1265`），由 `tick()` **每 tick 写一行**
并把 heartbeat 写进账本（`autonomous_life_resident.py:720-731`）。也就是说 resident journal 是
tick 密集的，「稀疏」来自**长 tick / 没有 tick**（长 owner 或模型调用把 tick 挤住、进程停、崩溃），
不是来自 quiet 合并。

**纠正 B（「一个合法安静夜必然被算成 gap」这一条过强）。** 在 poll=1800、阈值 4500s 下，一个
每 30 分钟一 tick 的安静夜根本不会产生 gap，旧代码不会把它判成 interrupted。旧代码真正的错是
**基准**：把墙钟跨度当覆盖，任何超过阈值的 tick 间隔一律记 downtime（真停机、被阻塞的单轮、
崩溃后的空档三者不可分）。所以本轮修的是「谁有权当覆盖」，不是把某个夜间情形改判。

结论：父级审计的方向（两套「一天」口径并存、报告不能由 journal 推断在线）成立，但具体机制与
后果按上面两条更正。

## 3. 现在的行为（逐条对应施工单 R）

1. **复用同一本账，不开新计时库。** `build_report` 只调
   `resident.recorded_coverage(now=moment)`（`one_day_life.py:1444`），它内部就是既有的
   `life_runtime_coverage.coverage()`；没有任何新的 store/文件/心跳补造。报告从不写
   `resident_coverage.json`，也不写 journal。
2. **五种量分开报，安静不再等于停机。** `window` 里有
   `closed_process_seconds` / `open_tail_seconds` / `covered_seconds`(=coverage_seconds) /
   `offline_gap_seconds`(=downtime_seconds) / `unknown_seconds` / `overlap_seconds` /
   `time_ambiguity` / `wall_span_seconds` / `coverage_complete` / `continuous` / `measured`（完整纯视图）。
   journal 的首末与间隔仍然报（`journal_span_seconds` / `first_journal_at` / `last_journal_at`），
   但**不参与判决**，并明确标注 `downtime_seconds_basis=JOURNAL_WAKE_GAP_UNVERIFIED_NOT_DOWNTIME`。
3. **已闭合 vs 当前 tail 分开。** `window.current_tail`：`open`、`last_process`、`last_edge`、
   `since_last_edge_seconds`、`unmeasured_after`、`unmeasured_seconds`。闭合的进程只贡献
   `closed_process_seconds`；未闭合行贡献 `open_tail_seconds`（它自己最后一次 heartbeat 之前的测量），
   之后到 `now` 的距离只作为距离报出，永远不算覆盖、也不算「停机」（它是 unknown）。
4. **PASS 只认账本。** 判定序：
   `无记录 → INSUFFICIENT_EVIDENCE`（`no tick was ever journalled and no process coverage ledger exists`）
   → `账本+running → RUNNING`（运行中不是闭合的一天）
   → `账本 && covered≥target && coverage_complete && continuous && 无歧义 → PASS`
   → `账本 && covered≥target 但不连续/有 unknown/有歧义 → INTERRUPTED`（basis 点名各秒数）
   → `账本 && covered<target → NOT_PASS`
   → `只有 journal 或只有旧 summary → NOT_PASS + unverified`（**永不 PASS**）。
5. **到阈值≠SOAK_VERIFIED。** `soak.soak_24h` 由 `soak_labels()` 从 covered 秒数算，
   `soak_verified` 恒 `NOT_VERIFIED`，`soak.note` 写明「一天是审阅者判断」；报告里没有任何字段
   取值为 `SOAK_VERIFIED*`（用例断言遍历所有字符串值）。
6. **零产出/零请求不判死，技术不可用不称休息。** `quiet` 块：`reading ∈ {OWNER_OUTPUT_PRESENT,
   QUIET_AND_UNBLOCKED, TECHNICAL_MODEL_UNAVAILABLE, TECHNICAL_BLOCKED, UNVERIFIED_NO_RECORD}`，
   带 `owner_output` / `requests`+`requests_known` / `requests_source` / `model_probe` /
   `blocked_reasons` / `reads_as_selected_rest: false`。判据只看 summary 自己的计数与 blocked_reasons、
   model probe：`MODEL*` 类 blocked 或 probe `UNREACHABLE` → 技术不可用；有其它 blocked → 技术阻塞；
   否则零产出 → `QUIET_AND_UNBLOCKED`（合法安静）。任何一支都不会被写成「她选择休息」。
7. **旧字段兼容且 basis 清楚。** 见 §4。

## 4. 字段与兼容（旧读者仍可用，但基准写在旁边）

| 键 | 有账本时 | 没有账本时 |
|---|---|---|
| `window.coverage_seconds` | covered（各进程 monotonic 之和） | journal 首末跨度（`observed_seconds_basis=JOURNAL_WAKE_SPAN_UNVERIFIED`） |
| `window.downtime_seconds` | 实测 offline gap | journal 未命名间隔（`downtime_seconds_basis=JOURNAL_WAKE_GAP_UNVERIFIED_NOT_DOWNTIME`） |
| `window.observed_seconds` | covered | `coverage - downtime`（journal 口径，仍标注 UNVERIFIED） |
| `window.gaps` | 账本的 `OFFLINE`/`OVERLAP` 条目（带 `ambiguous`） | journal 间隔条目 |
| `window.soak_label` | `soak_labels()` 的 `soak_24h` | 同左（**不再透传 summary 里的旧标签**） |
| `window.starts` / `ticks` / `started_at` / `stopped_at` / `running` / `target_seconds` / `first_journal_at` / `last_journal_at` | 原样保留 | 同左 |
| `verdict` / `verdict_basis` / `activity` / `model` / `pause` / `journey` / `actions` / `pending_unknown` / `travel` / `work` / `not_claimed` / `read_only` / `kind` / `subject_id` / `generated_at` | 原键保留 | 同左 |
| 新增 | `window.coverage_basis`(`PROVEN_PROCESS_LEDGER`)、`window.measured`(纯 coverage 视图)、`window.current_tail`、`window.unknown[]`、`soak`、`quiet`；`build_report(..., now=)` | 同左（`coverage_basis` 为 `JOURNAL_ONLY_UNVERIFIED` / `LEGACY_SUMMARY_WALL_SPAN_UNVERIFIED` / `NO_COVERAGE_RECORD`） |

`generated_at` 用注入的 `now`（默认真实 UTC），所以测试里可以把记录放到一年之外，但报告不会因此
多出任何覆盖秒数。

## 5. 用例与验证（12 条，全部注入时钟；不真等 24h、不起进程、不连 owner、不调模型）

`tests/test_life_report_coverage_v04.py`：

| # | 用例 | 断言要点 |
|---|---|---|
| 1 | `LedgerAuthorityTest.test_a_long_journal_gap_is_not_downtime_when_the_ledger_measured_the_process` | journal 两条记录相隔 12h（阈值 4500s），账本测到 86400s 且已闭合 → `downtime_seconds=0`、`gaps=[]`、`journal_span_seconds=43200`、`PASS` |
| 2 | `...test_a_journal_without_a_ledger_cannot_pass_and_says_why` | 只有 journal → `JOURNAL_ONLY_UNVERIFIED`、`NOT_PASS`、`cannot pass`、旧键仍在（12600/12600/0）、`soak_verified=NOT_VERIFIED` |
| 3 | `...test_a_real_stop_and_start_is_measured_offline_time_not_life` | 两进程各 12h、中间 2h 停机 → covered 86400、downtime 7200、`gaps=[OFFLINE]`、`continuous=False`、`INTERRUPTED`、`soak_24h=SOAK_RUNNING_24H` |
| 4 | `...test_a_crash_leaves_an_unknown_tail_and_no_passing_day` | 未闭合行 → closed 43200 / open tail 3600 / unknown 32400（`UNCLOSED_INTERVAL`）、`current_tail.open`、`NOT_PASS` |
| 5 | `...test_a_running_process_is_reported_as_an_open_tail_not_a_closed_day` | running + 未闭合行 → `RUNNING`、closed 0、open tail 1800、`unmeasured_after` = 最后 heartbeat、unknown 1800 |
| 6 | `...test_a_legacy_summary_wall_span_is_unknown_and_cannot_pass` | 旧 summary 90000 + 旧标签 `SOAK_THRESHOLD_REACHED_24H` → `LEGACY…UNVERIFIED`、coverage 0、unknown 90000、`NOT_PASS`、报告 `soak_24h=SOAK_RUNNING_24H` |
| 7 | `...test_two_records_that_disagree_about_wall_time_are_ambiguity_not_a_day` | 重叠 21600 → covered 86400 一分不减、`overlap_seconds=21600`、`covered_exceeds_wall_span`、`time_ambiguity`、`INTERRUPTED` |
| 8 | `ThresholdAndQuietTest.test_a_reached_threshold_is_not_soak_verified` | 25h 连续 → `PASS` + `SOAK_THRESHOLD_REACHED_24H` + `soak_verified=NOT_VERIFIED`；遍历所有字符串值断言无 `SOAK_VERIFIED*` |
| 9 | `...test_zero_owner_output_and_zero_requests_are_not_read_as_dead_or_as_rest` | 零 actions/零 requests 的 24h → 仍 `PASS`、`quiet.reading=QUIET_AND_UNBLOCKED`、basis 含 `zero-action` 与「not evidence that she was not alive」「not a recorded choice to rest」 |
| 10 | `...test_an_unreachable_model_is_technical_failure_not_a_chosen_rest` | `blocked_reasons={BLOCKED: MODEL_CONNECTION:5}` + probe `UNREACHABLE` → `TECHNICAL_MODEL_UNAVAILABLE`、basis「not her choosing to rest」、`NOT_PASS` |
| 11 | `...test_an_injected_clock_proves_nothing_about_a_real_day` | 记录只测到 1800s，`now` 放到 365 天后 → `wall_span=1800`（那一年不加覆盖）、`current_tail.since_last_edge_seconds=365d-1800`、`NOT_PASS`、`NOT_VERIFIED` |
| 12 | `...test_a_silent_instance_has_no_evidence` | 无 journal 无账本 → `INSUFFICIENT_EVIDENCE`、`NO_COVERAGE_RECORD`、`read_only=True` |

命令与退出码（`PYTHONDONTWRITEBYTECODE=1`，cwd = repo `tests/`，夹具目录 `mkdtemp` 不清理、保留为证据）：

```
python -m unittest -v test_life_report_coverage_v04
# Ran 12 tests ... OK，PYTHON_EXIT=0
# 日志：$env:PI_SCRATCH_DIR\r5-life-report-coverage-gen2-20261027.log（gen1 红证保留在
#       r2-life-report-coverage-20261027.log：第 8 条曾误把 note 里的 SOAK_VERIFIED 当标签、
#       第 11 条曾误以为 wall span 会随 now 增长；两条按真实语义改正后才绿）
```

精准受影响旧用例（只选少数，不重跑旧 14 条 coverage 绿组、不跑全套）：

```
python -m unittest -v \
  test_day_life_runtime_coverage.CoverageArithmeticTest.test_two_processes_that_stopped_are_not_one_continuous_life \
  test_day_life_runtime_coverage.CoverageArithmeticTest.test_the_threshold_labels_never_mint_a_verification \
  test_day_life_runtime_coverage.ResidentRecordTest.test_the_summary_reports_proven_coverage_not_the_wall_span
# Ran 3 tests ... OK，PYTHON_EXIT=0（日志 $env:PI_SCRATCH_DIR\r3-affected-runtime-coverage-20261027.log）
```

CLI 端到端（真进程、真文件读，不连 owner、不调模型）：

```
python components/life/scripts/one_day_life.py report --instance $env:PI_SCRATCH_DIR\cli-report-fixture
# REPORT_EXIT=0
# verdict PASS / coverage_basis PROVEN_PROCESS_LEDGER / coverage 90000 / closed 90000 / open tail 0
# / unknown 0 / soak_24h SOAK_THRESHOLD_REACHED_24H / soak_verified NOT_VERIFIED
# 输出留存：$env:PI_SCRATCH_DIR\cli-report-20261027.json
```

## 6. 会变红的一条旧用例（具名，未改：不在本写集）

`tests/test_one_day_life.py::ReportTest::test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not`
在新规则下**必然红**：它只写 journal + summary（`_write_records`，`tests/test_one_day_life.py:882-892`），
没有 `resident_coverage.json`，而 journal-only 已经不能 PASS。本机（Windows）该类整体
`skipIf(os.name == "nt")`，实测 `Ran 4 tests ... OK (skipped=4)`，所以这条后果是用**等价夹具探针**
测出来的（`$env:PI_SCRATCH_DIR\probe_old_report_rows.py`，输出在
`probe-old-report-20261027.log`，`PYTHON_EXIT=0`）：

```
premature        verdict=NOT_PASS  coverage_basis=JOURNAL_ONLY_UNVERIFIED window.observed=0     → 旧断言仍成立
full-idle        verdict=NOT_PASS  basis 含 "zero-action"                                      → 旧断言仍成立
full-with-result verdict=NOT_PASS  （旧断言期待 PASS）                                          → 这条会红
full-with-gap    verdict=NOT_PASS  （旧断言期待 INTERRUPTED）                                   → 随上一条一起红
```

最小修法（父级决定是否要我做）：在 `ReportTest._write_records` 里同时写一份
`resident_coverage.json`（用 `life_runtime_coverage.new_process_record/close_process_record` 造一行
已闭合进程，`online_seconds` 与 journal 跨度一致），旧断言的第二个分支（gap→INTERRUPTED）则改成
两段已闭合进程 + 中间 offline gap。**我没有改它**：`tests/test_one_day_life.py` 不在本批写集，
且它在 Windows 上无法验证改动是否真的绿。

> 补正（2026-10-27 晚，父级授权）：该用例与它的类专用夹具 helper 已改，见 §8 —— 账本补上后
> 这条在 WSL 上已绿。§6 的记录是当时那一批的交付状态，不改写。

## 7. 剩余限制（具名，不假装达标）

1. **没有真实 24h。** 本轮全部是注入时钟，`soak_verified` 仍是 `NOT_VERIFIED`；测试的绿不构成
   「她真的活了一天」，也不构成一只真实常驻实例的覆盖证据。
2. **没有接真实实例/真实模型。** 报告没有在原实例上跑过（原实例未触碰），`quiet` 的
   `TECHNICAL_MODEL_UNAVAILABLE` 只在夹具里验过机制。
3. **崩溃进程的覆盖上限是它最后一次 heartbeat**（既有 R 批语义），tick 之间那段算 unknown；
   报告如实报 unknown，不补心跳。
4. **`current_tail.unmeasured_seconds` 依赖 `now`**：一个已经死掉但 summary 仍写 `running: true` 的
   实例会被判 `RUNNING`（报告读的是它自己写的记录）。要判「真死了」需要外部存活证据，本批不造。
5. **报告仍会做少量只读副作用**（`life_pause.read` / request budget 视图 / action ledger 读取），
   与旧行为一致，`read_only: true` 的口径未变。
6. **旧 `test_one_day_life.py` 一条会红**，见 §6；当时未改。补正见 §8：只改了那一处夹具，用例已绿。
7. 未做：`py_compile`、全树 SHA、全树/全套回归、任何删除或 `TemporaryDirectory` 清理。

## 8. 补正回执：唯一一条具名受影响旧用例改为「写账本」，不是放宽断言

父级授权范围：只动 `tests/test_one_day_life.py::ReportTest` 这一个用例及必要的**类专用** helper；
不改产品、不改其它 tests、不重跑 12 条新用例、不全回归。

| 文件 | 改动 |
|---|---|
| `tests/test_one_day_life.py`（`ReportTest`，`:894-923`） | 新增类专用 helper `_write_coverage(windows, *, pid=9100)`：每行都由 `life_runtime_coverage.new_process_record` + `close_process_record` 造出**已闭合**（`closed=STOP`、`online_seconds_source=MONOTONIC_THIS_PROCESS`）的进程记录，按 `kind/schema_version/state_root/processes` 信封写到 `odl.resident_module.RESIDENT_COVERAGE_FILENAME`（即 `resident_coverage.json`）；`online_seconds` 由该行自己的 start→stop 跨度算出，不手写常数。 |
| 同上：`test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not`（`:948-969`） | PASS 分支补一行已闭合进程（00:00→次日 01:00，online 90000）；gap 分支补**两段**已闭合进程（`base[:20]` 34200s + `base[33:]` 54000s，中间 25200s 由账本自己算成 `OFFLINE` gap）。原硬断言一条不减且原样：`coverage_seconds > 86400`、`downtime_seconds > 0`、`VERDICT_INTERRUPTED`；另各加一条 `coverage_basis == COVERAGE_BASIS_LEDGER`，钉住「判决读的是账本」。 |

`ReportTest._write_records`（`:882-892`）**一字未动**：premature / full-idle 两条仍只写 journal，
继续用原断言证明「只有 journal 的窗口不能 PASS、零产出的窗口仍不能 PASS」——正因为它们没被喂账本，
新规则下的这两条断言还是有效的反证。

未动：`components/life/scripts/*`（产品三份文件）、`tests/test_life_report_coverage_v04.py`（12 条新用例）、
其它任何测试、`MANIFEST.json`、发布物、原实例、真实模型。

验证（WSL POSIX，只这一个 exact ID；`PYTHONDONTWRITEBYTECODE=1`；cwd = repo `tests/`；夹具根沿用既有
`/tmp/nyaw05r.jdK3q`，配合既有 `v03-retain-hooks/sitecustomize.py` 的保留式退役；没有物理删除、
没有 `TemporaryDirectory` 清理、没有新写全局 unlink noop）：

```
TMPDIR=/tmp/nyaw05r.jdK3q \
PYTHONPATH=<scratch>/v03-retain-hooks PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest -v test_one_day_life.ReportTest.test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not
# test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not ... ok
# Ran 1 test in 1.711s
# OK
# PYTHON_EXIT=0
```

日志 `<scratch>/r7-one-day-report-ledger-20261027.log`。夹具保留（未删）：
`/tmp/nyaw05r.jdK3q/{w05inst-0qcvq5z0,w05svc-1bi5yix4,w05world-nzgqg89b,w05pkg-ijq6q4v7}`。

实测数字（同一夹具上只读跑 `build_report`，不是推断）：

| 分支 | verdict | basis | coverage | downtime | closed | unknown | continuous |
|---|---|---|---|---|---|---|---|
| PASS 分支（单进程 90000s） | `PASS` | `PROVEN_PROCESS_LEDGER` | 90000 | 0 | 90000 | 0 | true |
| gap 分支（两进程 34200+54000） | `INTERRUPTED` | `PROVEN_PROCESS_LEDGER` | 88200 | 25200 | 88200 | 0 | false |

gap 分支账本原文：`9100` 00:00→09:30 `online_seconds=34200`，`9101` 16:30→次日 07:30
`online_seconds=54000`，`gaps=[{kind: OFFLINE, seconds: 25200, ambiguous: false}]`，
`soak_24h=SOAK_RUNNING_24H`（有洞就不是一天）、`soak_verified=NOT_VERIFIED`。

限制照旧：这条绿证明的是**报告读账本的判决路径**，不是「她真的活了一天」；`soak_verified` 仍
`NOT_VERIFIED`；没有跑 12 条新用例、没有全回归、没有 CLI、没有真实模型、没有 `py_compile`/SHA。
