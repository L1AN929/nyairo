# Agent Note: v0.4 一天报告改读覆盖账本 —— 安静不等于停机，阈值到达不等于 SOAK_VERIFIED

Status: implemented

## Problem

0.4 的口径是「主体能正常活一天」，而**同一棵树里有两套互不相容的「一天」**：

1. `autonomous_life_resident` + `life_runtime_coverage` 报的是「各进程自己 `time.monotonic()` 量到的
   covered 秒数」，把 wall 跨度、在线、停机、未知、时钟歧义分成五个量；
2. `one_day_life.build_report` 仍在用 `resident_journal.jsonl` 的**第一条与最后一条记录的墙钟瞬时**
   当 coverage，再把任何大于 `poll × 2.5` 的相邻间隔全算 downtime，`observed = coverage - downtime`。

第 2 套有三个具体后果：

- **墙钟不是测量。** 两个进程、一次重启、一次崩溃，在这套算术里长得一样；NTP 跳变与休眠会直接污染它。
  它可以在进程只活了 3 小时的情况下报出 90000s 的「观测」。
- **长 tick 间隔被当成停机。** 真正的停机、被一个长 owner/模型调用挤住的单轮、崩溃后没有 tick 的空档，
  三者不可区分，全部计成 downtime；反过来，只要 tick 密集，一个安静夜看起来毫无问题——所以问题不是
  「安静夜必然被算成 gap」，而是「谁有权当覆盖」这件事压根没定。
- **旧标签直接透传。** `window.soak_label = summary["soak_24h"]`，于是一份只有墙钟跨度的旧记录只要
  自己写着 `SOAK_THRESHOLD_REACHED_24H`，报告就照着抄。

另外两件事也没有地方表达：运行中的当前 tail 与已闭合的测量混在一个数里；零 owner 产出、零请求、
模型不可用、被 owner blocked 这四种完全不同的情况，在报告里都只会显示成「没有行动」。

## Decision

报告只从**同一本账**取数，并把「谁有权当覆盖」写进判定里：

1. **读账本，不开新计时库。** `build_report` 调新增的
   `AutonomousLifeResident.recorded_coverage(now=...)`（`autonomous_life_resident.py:374`），它内部就是
   既有的 `life_runtime_coverage.coverage()`，但以**别的进程**的视角读：`current_process_id=None`，
   未闭合行的尾部留在 `unknown`，旧 summary 的墙钟跨度只在没有账本时才进 `unknown`。报告不写账本、
   不写 journal、不补假心跳。
2. **五量分开 + 闭合/tail 分开。** 纯模块新增两个派生字段
   `closed_process_seconds`（测到**并且**已 STOP 的那部分）与 `open_tail_seconds`（测到但从未闭合的
   那部分），每进程投影补 `started_at` / `last_heartbeat_at`。报告把它们与
   `wall_span_seconds` / `covered_seconds` / `offline_gap_seconds` / `unknown_seconds` /
   `overlap_seconds` / `time_ambiguity` / `coverage_complete` / `continuous` 并列报出，并给
   `window.current_tail`（`open` / `last_edge` / `since_last_edge_seconds` / `unmeasured_after` /
   `unmeasured_seconds`）。
3. **PASS 只认账本。** 判定序：无记录 → `INSUFFICIENT_EVIDENCE`；账本 + running → `RUNNING`；
   账本 + covered ≥ target + `coverage_complete` + `continuous` + 无歧义 → `PASS`；账本 + covered ≥ target
   但断过/有未知/有歧义 → `INTERRUPTED`；账本 + covered < target → `NOT_PASS`；
   **只有 journal 或只有旧 summary → `NOT_PASS` + `*_UNVERIFIED`，永不 PASS。**
4. **journal 的数字仍然报，但只作为 journal 的事实。** `journal_span_seconds` / `first_journal_at` /
   `last_journal_at` 保留；旧键 `coverage_seconds` / `downtime_seconds` / `observed_seconds` / `gaps`
   也在，但各自带 `coverage_basis` / `downtime_seconds_basis` / `observed_seconds_basis`：
   有账本时它们就是 covered / 实测 offline gap；没有账本时它们是 journal 口径并明确
   `JOURNAL_WAKE_GAP_UNVERIFIED_NOT_DOWNTIME`，不参与判决。
5. **到阈值不是验证。** `soak.soak_24h` 由 `soak_labels()` 从 covered 秒数算，`soak_verified` 恒
   `NOT_VERIFIED`，`soak.note` 写明「一天是审阅者对 journal、账本与 owner 结果的判断」；用例遍历报告
   所有字符串值，断言没有任何字段取值为 `SOAK_VERIFIED*`。
6. **安静与技术不可用分开。** 新增 `quiet` 块：`reading ∈ {OWNER_OUTPUT_PRESENT,
   QUIET_AND_UNBLOCKED, TECHNICAL_MODEL_UNAVAILABLE, TECHNICAL_BLOCKED, UNVERIFIED_NO_RECORD}`，
   判据只用 summary 自己的 `counters` / `blocked_reasons` / `model.probe`。零产出、零请求不会被当成
   「没活」，也不会被写成「她选择休息」（`reads_as_selected_rest: false` 是显式字段，不是措辞）；
   模型不可用/被 blocked 一律标技术原因。
7. **`now` 可注入。** `build_report(..., now=)` 让测试把记录放到一年之外，但注入时钟**不增加任何覆盖
   秒数**：报告的 `wall_span_seconds` 仍然只覆盖记录本身，记录末端到 `now` 的距离只作为距离报出。

## Alternatives considered

- **继续用 journal，只把 gap 阈值调大/按 poll 自适应** —— 最强理由是「不动账本，旧记录也照样能读」。
  否决原因：阈值只是把「多长算停机」换个数字，基准仍然是墙钟：崩溃没有 tick、长调用挤住 tick、
  真停机三者永远不可分，而它给出的 `observed` 会被当成「在线秒数」。
- **让报告自己维护一份「一天计时」store** —— 最强理由是「报告就能自足，不必依赖 resident 的字段」。
  否决原因：那是第二份事实源，与各进程自己的 monotonic 测量重复，且两份记录一旦不一致，报告会比
  resident 更不可信；施工单也明确禁止新计时库。
- **legacy 记录按墙钟跨度给 PASS，只加一句「legacy」** —— 最强理由是「旧用例与旧回执不用改」。
  否决原因：这正是要修的谎。旧数据可以读、历史不改写，但**不能**冒充一天：它只进 `unknown`。
- **用 `summary.running` 判「在线秒数」** —— 最强理由是「一个布尔最简单」。否决原因：进程崩掉之后
  summary 仍写着 `running: true`（本批不改这条），所以它只能决定「这是不是一天已闭合」，不能证明
  任何一个秒数。现在它只影响 `RUNNING` 这个标签。
- **把 open tail 也计入 covered 后判 PASS** —— 最强理由是「连续跑了 24h 看起来就该 PASS」。否决原因：
  未闭合区间没有任何交付/关闭证据，`coverage_complete=False`；报告宁可给 `RUNNING`，把 tail 单列，
  也不把「还没结束」写成「已经活了一天」。
- **零产出时直接写「她休息了」** —— 最强理由是「读起来像人话，一天总得有个说法」。否决原因：她没有
  任何记录过的休息决定；把沉默翻成选择，既冤枉她，也掩盖真正的原因（模型不可用、被 blocked）。
  现在只给 `QUIET_AND_UNBLOCKED` 这种中性标签，并把技术原因单列。
- **把 `window` 的旧键删掉，只留新键** —— 最强理由是「口径清楚，不留歧义」。否决原因：旧读者
  （既有报告回执、运维脚本、历史文档）会直接 KeyError；现在旧键保留、基准写在旁边，读的人看得见
  「这个数字现在靠什么成立」。

## Consequences

- **收益**：日报告的 PASS 现在需要可证明的秒数（每进程自己的 monotonic + 真 STOP），停机、崩溃、
  时钟歧义各自具名；安静不再被翻译成停机；运行中的 tail 与已闭合测量分开；阈值到达不再冒充
  `SOAK_VERIFIED`；零产出与技术不可用不再混为一谈；注入时钟可以验证一年跨度的算术而不用等一天。
- **代价与已知上限**：
  1. **一条旧用例会红**：`tests/test_one_day_life.py::ReportTest::
     test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not` 只写 journal + summary，
     旧断言期待 PASS / INTERRUPTED；新规则下 journal-only 一律 `NOT_PASS`。该文件不在本批写集，未改，
     最小修法（补一份 `resident_coverage.json` 夹具）写在
     `docs/engineering/v04-life-report-coverage-result.md` §6。
  2. 崩溃进程的覆盖上限仍是它最后一次 heartbeat，tick 之间那段恒为 `unknown`（方向安全，覆盖率会略低）。
  3. `current_tail` 依赖 `now`：一个已死但 summary 仍写 `running: true` 的实例会被判 `RUNNING`；判「真死」
     需要外部存活证据，本批不造。
  4. 本轮**没有**真实 24h、没有真实模型、没有在原实例上跑；全部是注入时钟+夹具。
- **相邻既有决定**：[运行时间证据与 wall 跨度分开](./2026-10-26-v04-day-life-runtime-coverage.md)、
  [一天生活验收报告按 journal 算覆盖](../../../../docs/engineering/v02-autonomous-life-implementation.md)、
  [单一写者隔离](../process/2026-10-16-single-writer-isolation.md)。
- **机制与结果**：[R 侧交付结果](../../../../docs/engineering/v04-life-report-coverage-result.md)。

## Verification

- 新增 `tests/test_life_report_coverage_v04.py` 12 条（静默长间隔 / 只有 journal / 真停机 / 崩溃 /
  running tail / 旧 summary / 时钟歧义 / 阈值≠验证 / 零产出 / 模型不可用 / 注入时钟 / 无记录），
  `PYTHONDONTWRITEBYTECODE=1`、cwd = repo `tests/`：
  `python -m unittest -v test_life_report_coverage_v04` → `Ran 12 tests ... OK`，`PYTHON_EXIT=0`，
  日志 `$env:PI_SCRATCH_DIR\r5-life-report-coverage-gen2-20261027.log`（gen1 红证
  `r2-life-report-coverage-20261027.log` 保留：第 8 条曾误把 note 里的 `SOAK_VERIFIED` 当作标签、
  第 11 条曾误以为 wall span 会随 `now` 增长，两条按真实语义改正）。
- 精准受影响旧用例 3 条（不重跑 R 前一批的 14 条绿组、不跑全套）：
  `test_day_life_runtime_coverage.CoverageArithmeticTest.test_two_processes_that_stopped_are_not_one_continuous_life`、
  `...test_the_threshold_labels_never_mint_a_verification`、
  `...ResidentRecordTest.test_the_summary_reports_proven_coverage_not_the_wall_span`
  → `Ran 3 tests ... OK`，`PYTHON_EXIT=0`，日志
  `$env:PI_SCRATCH_DIR\r3-affected-runtime-coverage-20261027.log`。
- 旧 `ReportTest`（POSIX-only）本机实测 `Ran 4 tests ... OK (skipped=4)`；它在新规则下的后果用等价夹具
  探针实测（`probe_old_report_rows.py` → `probe-old-report-20261027.log`，`PYTHON_EXIT=0`）：
  premature / full-idle 仍 `NOT_PASS` 且断言仍成立，full-with-result / full-with-gap 由 PASS/INTERRUPTED
  变成 `NOT_PASS`（唯一会红的旧用例，见 Consequences）。
- CLI 端到端（真进程、真文件读、不连 owner 不调模型）：
  `python components/life/scripts/one_day_life.py report --instance $env:PI_SCRATCH_DIR\cli-report-fixture`
  → `REPORT_EXIT=0`，`verdict=PASS`，`coverage_basis=PROVEN_PROCESS_LEDGER`，`coverage 90000 / closed 90000 /
  open tail 0 / unknown 0`，`soak_24h=SOAK_THRESHOLD_REACHED_24H` + `soak_verified=NOT_VERIFIED`
  （输出 `$env:PI_SCRATCH_DIR\cli-report-20261027.json`）。
- 未做：`py_compile`、全树 SHA、全套/全树回归、真实 resident 启动、真实模型、任何删除与
  `TemporaryDirectory` 清理（夹具全部保留）。
