# Agent Note: 旧报告用例的夹具改成「写覆盖账本」，而不是把断言放松

Status: implemented

## Problem

R 把 `one_day_life.build_report` 的判决权从 `resident_journal.jsonl` 的首末瞬时换到同 root 的
`resident_coverage.json`（见 [feature/2026-10-27-v04-life-report-coverage](../feature/2026-10-27-v04-life-report-coverage.md)）：
只有 journal 的窗口一律 `JOURNAL_ONLY_UNVERIFIED` + `NOT_PASS`，永不 PASS。

`tests/test_one_day_life.py::ReportTest._write_records` 只写 journal + summary，于是**唯一一条**
具名受影响的旧用例 `test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not`
必然变红：它的两个分支原本期待 `PASS` 与 `INTERRUPTED`，而实测（等价夹具探针
`probe_old_report_rows.py`）都是 `NOT_PASS / JOURNAL_ONLY_UNVERIFIED`。这条用例是旧口径的
**证据来源**（"journal 跨度够长 + 有 owner 结果 = 一天"），不是随手写的断言。

## Decision

只改这一个用例和它的**类专用** helper，产品与其它 tests 一行不动：

1. `ReportTest._write_coverage(windows, *, pid=9100)` 写一份**真账本**：每行由
   `life_runtime_coverage.new_process_record` + `close_process_record` 造出，`closed=STOP`、
   `online_seconds_source=MONOTONIC_THIS_PROCESS`，`online_seconds` 由该行自己的 start→stop
   跨度算出（不手写常数）；信封键 `kind/schema_version/state_root/processes`，文件名取
   `odl.resident_module.RESIDENT_COVERAGE_FILENAME`，不另立第二份事实源。
2. 该用例两个分支各补一次账本：PASS 分支一行已闭合进程（00:00→次日 01:00，online 90000）；
   gap 分支**两段**已闭合进程（34200s + 54000s），中间 25200s 由账本自己算成
   `kind=OFFLINE` 的 gap。
3. 原硬断言原样保留：`coverage_seconds > 86400`（实测 88200）、`downtime_seconds > 0`
   （实测 25200）、`VERDICT_INTERRUPTED`；另各加一条 `coverage_basis == COVERAGE_BASIS_LEDGER`，
   让「判决读的是账本」这件事被钉住。
4. `_write_records` 一字未动：`test_report_never_passes_prematurely_and_counts_downtime` 与
   `test_a_full_but_idle_window_is_not_a_pass` 仍只写 journal，继续当**反证**（journal-only 不能
   PASS、零产出不能 PASS）使用。

## Alternatives considered

- **把 R 的判决改回去，让 journal-only 继续 PASS** —— 最强理由是「旧用例、旧回执都不用动，改动最小」。
  否决原因：这正是 R 修掉的谎（墙钟跨度冒充测量、真停机/崩溃/长 tick 不可分），为了迁就一个夹具
  撤回承重墙是倒因为果。
- **删掉这条用例** —— 最强理由是「新用例 `test_life_report_coverage_v04.py` 的 12 条已经覆盖了口径」。
  否决原因：那 12 条都在合成的 state root 上跑；这条是唯一在**真实 `prepare` 出来的实例**上端到端
  验「报告对一份 25 小时记录给 PASS / 对有停机的记录给 INTERRUPTED」的用例，删了就没有实例级证据。
- **把断言改成一律 `NOT_PASS`** —— 最强理由是「红改绿最快」。否决原因：那样这条用例既不再证明
  「一天可以 PASS」，也不再区分"停机"与"正常"，变成只验证自己刚写下的东西。
- **在夹具里手写一行 `resident_coverage.json`（JSON 字面量）** —— 最强理由是「最短」。否决原因：
  手写行会和账本格式各自漂移；用产品自己的构造器时，格式变了测试会跟着变，而不是静默造出一份
  无效账本（`coverage()` 对 `online_seconds=None`、`closed` 缺省等都会按未知处理）。
- **用 `AutonomousLifeResident._coverage_write` 写盘** —— 最强理由是「连信封都跟产品一致」。否决原因：
  那是私有方法，测试一旦依赖私有写盘路径，产品重构就会以"测试坏了"的形式出现；信封的三个常量
  （`KIND`/`SCHEMA_VERSION`/文件名）本来就是公开的，够用。

## Consequences

- 收益：旧用例重新变成有效证据——同一份 journal，账本闭合就给 `PASS`，两段进程中间有实测
  停机就给 `INTERRUPTED`；`coverage_seconds`/`downtime_seconds` 现在真是账本数字。
- 代价：`tests/test_one_day_life.py` 多了一个 30 行的类专用 helper；夹具变重（要写两个文件而不是一个）。
- 没有改变的事实：这条绿**不**证明真实 24h；`soak_verified` 仍 `NOT_VERIFIED`；没有真实模型、
  没有在原实例上跑。
- 相邻决定：[R 侧机制与结果](../../../../docs/engineering/v04-life-report-coverage-result.md) §6/§8、
  [运行时间证据与 wall 跨度分开](../feature/2026-10-26-v04-day-life-runtime-coverage.md)。

## Verification

WSL POSIX，只跑这一个 exact ID（`PYTHONDONTWRITEBYTECODE=1`，cwd = repo `tests/`，夹具根
`/tmp/nyaw05r.jdK3q` + 既有 `v03-retain-hooks/sitecustomize.py` 的保留式退役，无物理删除、
无 `TemporaryDirectory` 清理、无新增全局 unlink noop）：

```
python3 -m unittest -v test_one_day_life.ReportTest.test_a_full_window_with_a_real_result_passes_and_a_stopped_run_does_not
# ... ok / Ran 1 test in 1.711s / OK / PYTHON_EXIT=0
```

日志 `$env:PI_SCRATCH_DIR\r7-one-day-report-ledger-20261027.log`。同一夹具上只读跑 `build_report`
的实测：PASS 分支 `coverage 90000 / downtime 0 / closed 90000 / continuous true`；gap 分支
`coverage 88200 / downtime 25200 / gaps=[OFFLINE 25200] / continuous false / INTERRUPTED`，
两分支 `coverage_basis=PROVEN_PROCESS_LEDGER`、`soak_verified=NOT_VERIFIED`。

未做：12 条新用例重跑、全回归、CLI、真实模型、`py_compile`、全树 SHA。
