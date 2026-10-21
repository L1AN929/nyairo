# Agent Note: v0.4 运行时间证据 —— wall 跨度、进程在线、停机间隔、未知四件事分开

Status: implemented

## Problem

0.4 的验收标准是"主体能正常活一天"，但当前树里唯一声称"活了多久"的东西是
`resident_summary.json` 的 `observed_seconds`，它算的是 `now - soak_started_at`——**第一次启动至今的墙钟跨度**。
这个数在三种完全不同的情况下都会涨：她在跑、她停机了、机器重启了。于是：

1. **停机被算成生活。** 一个只在 24 小时里跑了 3 小时的实例，只要中间没人在 24 小时内看，
   `soak_24h` 就会到 `SOAK_THRESHOLD_REACHED_24H`。没有任何地方能证明"这 24 小时里进程真的在"。
2. **跨进程的 monotonic 无处安放。** `time.monotonic()` 是每进程、每次开机各一份的，重启后归零；
   旧记录只有 wall 时间戳，所以"上一段在线了多久"根本无从证明——既不等于两进程的 wall 差，
   也不能把两段 monotonic 相加。
3. **异常中断的最后一段被默认成全程在线。** 进程崩在两次 tick 之间时，从最后一次 tick 到下一次启动
   这段既没有 stop 记录也没有测量；旧记录只能拿"启动时间"当证据，等于替崩溃补了一段生活。
4. **`run()` 的时长会被 poll 拖过头。** deadline 用的是 wall（正确），但等待循环每次都睡满
   `poll_seconds`（按 1 秒切片），`--duration-seconds 60 --poll-seconds 180` 实际要 3 分钟才停，
   停机指令之外的路径上等于多跑了两分钟。
5. **`ACTED` 一律被记成 owner 确认版本。** `_count` 只要 `status_class == acted` 且 `outcome == ACTED`
   就给 `owner_confirmed_versions += 1`；可 `ACTED` 只说明这条链路跑完了，不说明 owner 真的产生了新版本
   （STEP 可能被拒、adoption 可能被拒、可能只是 ALREADY_DONE）。报告因此会夸大"作品变化"。
6. **模型请求总计漏掉私人阅读响应。** 无 ledger 时 `total_requests = cognition + cycle.writer.call_count`，
   而 `reading_response` 是**另一个** writer（`cycle.reading_response_writer`，真实 provider 请求、
   真实预算准入），它一次都没被算进去；有 ledger 时 `content_generation_calls` 也只读作品 writer。

## Decision

新增纯模块 `components/life/scripts/life_runtime_coverage.py`，只给 resident 加一层"进程账本"，
不改 cycle/runner/assembly/one_day_life/phone/world，也不新建第二个 owner：

1. **四件事分开，互不派生。** 账本 `resident_coverage.json` 每进程一行（`process_id` = host:pid:启动瞬间、
   `started_at`、`last_heartbeat_at`、`online_seconds`（本进程自己的 monotonic 测量）、`closed`）。
   汇总出 `wall_span_seconds`（墙钟跨度，含停机）、`covered_seconds`/`process_online_seconds`
   （各进程测量之和，**不跨进程相加、不跨 gap 推断**）、`offline_gap_seconds`（相邻进程 close→start 的
   wall 差，钳到 0）、`unknown_seconds`（未闭合尾部 + 无测量区间 + 旧 summary 的墙钟跨度）。
2. **旧 summary 的墙钟跨度只能进 unknown。** 没有账本的旧记录（只有 `observed_seconds`）读到后按
   `sources.legacy_summary=PRESENT_UNPROVEN` 计入 `unknown_seconds`，`covered_seconds=0`，
   `soak_24h` 保持 `SOAK_RUNNING_24H`——旧数据可读、历史不改写、也不允许它冒充一天。
3. **未闭合区间永远是洞。** 启动时只 append 一行 `UNCLOSED`，tick 时写 heartbeat，`stop()` 才 `close`。
   崩溃留下的行不"修复"：它只承认最后一次 heartbeat 的测量，之后到下一次启动（或 now）计入
   `unknown_seconds`，`coverage_complete=False`。
4. **完整性分两层。** `coverage_complete` = 每个区间都有交代（都 STOP 关闭、都有测量、无 unknown）；
   `continuous` = 在此基础上没有 offline gap。`soak_24h`/`soak_7d` 只在 `continuous` 时才 REACHED；
   词表逐字沿用旧的四个值，`soak_verified` 恒为 `NOT_VERIFIED`——"她真的活了一天"是审阅者对 journal、
   账本与 owner 结果的判断，不是两个时钟的算术。
5. **`observed_seconds` 保留为兼容键但换基准。** 值改为 `covered_seconds`，并新增
   `observed_seconds_basis`、`wall_span_seconds`、`process_online_seconds`、`offline_gap_seconds`、
   `unknown_seconds`、`coverage_complete`、`continuous`、`wall_rollback`、`coverage`。旧键还在，
   但不再能把停机算成生活；`one_day_life.py` 用 journal 自己算 coverage，不受影响。
6. **`run()` 的等待被 deadline 裁剪。** `remaining = deadline - now`，`wait = min(poll_seconds, remaining)`，
   `remaining<=0` 直接退出；返回值分开 `process_online_seconds`（monotonic）与 `wall_seconds`（wall）。
   有界停止与信号释放 lease 的行为不变。
7. **`owner_confirmed_versions` 要证据。** 只有 wake 的 `extra` **指名**了 owner 版本
   （`owner_version_id`/`owner_version_ref` 非空，或 `owner_version_created is True` 且有 `step_path`）
   才计入并记 ref（上限 20 条）；否则进 `owner_version_unknown`，`actions` 仍表示 ACTED 窗口数。
   runner 现在只写 `extra={"budget": ...}`，所以这个计数暂时恒为 0——这是正确结果，不是回归。
8. **模型请求按表面分开计。** `content_generation_calls` = 作品 writer + 阅读响应 writer 的 `call_count`；
   新增 `reading_response_calls`/`surfaces`/`reading_response_counted`；缺表面报 `None`（unknown）不是 0；
   有 ledger 时 `total_requests` 取账本 `utc_day_calls`（含 decision/content/reading_response/retry），
   账本存在但读不出时 `total_requests=None` + `LEDGER_UNREADABLE`，绝不写成 0。
9. **负 gap 是具名时间歧义，不是连续。** 后一条记录的开始早于前一条的停止时，记 `gaps[].kind="OVERLAP"`
   （`ambiguous=true`，带真实重叠秒数）+ 顶层 `overlap_seconds`；`covered_exceeds_wall_span` 标出"覆盖大于
   它发生的跨度"；`time_ambiguity = wall_rollback or overlap>0 or covered_exceeds_wall_span`；`continuous`
   在歧义时为 **False**（`soak_*` 因此保持 `RUNNING`）。`wall_rollback` 只表示"跨度本身为负"，不冒充重叠；
   真实 monotonic 秒数一分不减（那是测到的事实），重叠也不被当成停机。
10. **一个洞只记一次。** 无 monotonic 且未闭合的行：无测量部分只到本行**最后一次 heartbeat**，之后才是
   未闭合尾巴；两段相接不重叠。`_record_unknown()` 同时产出条目与要加的秒数（零秒不记），因此
   `sum(entry["seconds"]) == unknown_seconds` 恒成立，两个 reason 覆盖的是洞的两个不同片段。

## Alternatives considered

- **直接把 `observed_seconds` 改成"本次进程 uptime"** —— 最强理由是"一个数最不容易被误读"。否决原因：
  跨进程、跨重启的"这个 state root 一共活过多久"就彻底没了，而这正是 24h 验收要的东西；而且会悄悄改变
  既有读者对"这个实例观测了多久"的理解。现在旧键保留（基准改成可证明的覆盖秒数），其余三个量并列报出。
- **用 wall 时间戳算每段在线（`stopped_at - started_at`）** —— 最强理由是"旧记录也有时间戳，历史可以回算"。
  否决原因：那正好是这次要修的错——墙钟回拨、NTP 跳变、挂起休眠都会污染它，而且"进程在"与"墙在走"是两件事。
  现在每段在线只认该进程自己的 `time.monotonic()`；没测量就是 unknown。
- **把崩溃进程的行在下次启动时"补关"到下次启动时刻** —— 最强理由是"账本更整齐，覆盖率也更高"。否决原因：
  那等于替一个没跑完的进程编造它跑完了，正是"异常中断最后区间不能默认为全程在线"。现在该行永远保持
  `UNCLOSED`，尾部进 unknown。
- **把 offline gap 也算进 `coverage_complete=False`** —— 最强理由是"有停机就不是完整的一天"。否决原因：
  完整性和连续性是两个问题，混在一起会让"有停机但每一段都已知"的账本看起来像"账本本身不可信"。
  现在 `coverage_complete` 只回答"每段是否都有交代"，`continuous` 回答"中间有没有断"，soak 门槛用后者。
- **`ACTED` 且带 `action_id` 就当作 owner 版本** —— 最强理由是"action id 已经指向一次真实提交"。否决原因：
  action id 只证明提交被受理，不证明 owner 产生了新版本（可能被拒、可能 ALREADY_DONE），把两者等同就是
  旧缺陷。现在必须由 runner 在 `extra` 里指名版本；缺证进 unknown。
- **在 resident 里直接读 owner 的版本文件** —— 最强理由是"resident 已经有 runtime 句柄，读一下最省事"。
  否决原因：那是绕过 runner 的 wake 记录去自造第二份 owner 事实，而且 resident 并不知道这一轮到底改了
  哪个版本。现在只读 wake 的 `extra`，字段契约写进 `docs/engineering/v04-day-life-runtime-result.md`，
  由 runner 的写者（B）决定何时透传。
- **把负 gap 钳成 0 并继续算连续（旧行为 `OVERLAP_IGNORED`）** —— 最强理由是"两个进程本来就不该同时持账本，
  这大概只是记录噪声，钳掉最省事"。否决原因：钳掉等于把"两条记录对同一段 wall 时间互相矛盾"这件事从账上
  抹掉，于是 `covered 7200 > wall 1800` 还能报 `continuous=True`，直接变成"重叠也能当连续阈值证据"。现在负 gap
  记 `OVERLAP`+`ambiguous`+真实秒数，`time_ambiguity=True`、`continuous=False`，soak 门槛保持 `RUNNING`。
- **重叠时把 `covered` 裁到 wall 跨度以内** —— 最强理由是"覆盖不该超过它发生的墙钟跨度，裁一下数字就自洽了"。
  否决原因：那是在删真实的 monotonic 测量（两个进程各自确实在线了 3600 秒），把记录问题改写成"她其实只活了
  1800 秒"。现在秒数一分不减，矛盾由 `overlap_seconds`/`covered_exceeds_wall_span`/`time_ambiguity` 具名承担。
- **把重叠当停机补进 `offline_gap_seconds`** —— 最强理由是"反正都是没活的时间"。否决原因：重叠不是"没在活"，
  是"两条记录不能同时为真"，记成停机会让停机统计也被污染。现在停机与歧义各自成量。

## Consequences

- **收益**：`resident_summary.json` 现在能回答四个不同的问题——墙钟过了多久、进程真的在线多久、停机多久、
  还有多少不知道；跨进程与重启不再把停机补成生活；崩溃的最后一段显式留成 unknown；`run()` 的时长不再被
  poll 拖长；`owner_confirmed_versions` 与模型请求总计都不再把"跑完了"当成"有结果"。全部逻辑在纯模块里，
  可以用注入时钟把两个进程放到一年之外验证，不用等一天。
- **代价与已知上限**：① 本轮**没有**真实 24h 证据，也没启动常驻、没有 provider、没花额度；
  ② `owner_confirmed_versions` 暂时恒为 0，等 B 在 runner 的 `extra` 里透传版本契约；
  ③ 崩溃进程的覆盖上限是它最后一次 heartbeat，tick 之间的那段算 unknown（方向安全，但覆盖率会略低）；
  ④ `journey_view()` 依赖 POSIX `fcntl`，Windows 测试机上 summary 用例对该视图做了实例级替身
  （生产 Linux 不受影响，属既有平台限制，不在本写集修）；
  ⑤ `one_day_life.py` 仍把 `counters.owner_confirmed_versions` 叫 "owner_confirmed_results"，计数变严格后
  该行可能更小（该文件不在本写集，未改）。
- **gen2 修正**：重叠记录不再被当作安全连续（`continuous` 为 False、`overlap_seconds`/`time_ambiguity` 具名），
  无测量未闭合行的洞不再双算（条目和恒等于 `unknown_seconds`）。代价：账本里出现 `OVERLAP` 时，
  汇总会拒绝给连续标签，需要人去看时钟/记录，而不是自动"修好"。
  已知上限：`coverage_complete` 仍可能为 True（账目完整）而 `continuous=False`，读报告的人必须看后者。
- 相邻既有决定：[一天生活验收报告按 journal 算覆盖](../../../../docs/engineering/v02-autonomous-life-implementation.md)、
  [单一写者隔离](../process/2026-10-16-single-writer-isolation.md)、
  [世界身体常驻与休息](./2026-10-21-v04-world-body-resident-and-rest.md)。
- 机制与结果：[R 侧交付结果](../../../../docs/engineering/v04-day-life-runtime-result.md)。

## Verification

- gen1：跑新增类全部 exact IDs（`tests/test_day_life_runtime_coverage.py` 的 `CoverageArithmeticTest` 与
  `ResidentRecordTest`，12 条），`PYTHONDONTWRITEBYTECODE=1`、repo `tests/` cwd：
  `python -m unittest test_day_life_runtime_coverage -v` → `Ran 12 tests ... OK`，`EXIT=0`，
  日志 `$env:PI_SCRATCH_DIR\run3-runtime-coverage-20261018-195522.log`（每次运行文件名唯一，失败保留）。
- gen2：父级只读 probe（`scratch/day-life-coverage-parent-boundaries-gen1.log`）复现了两个具体缺陷，本轮
  **只跑新增这 2 条**（未重跑 gen1 的 12 条）：
  `python -m unittest -v test_day_life_runtime_coverage.CoverageArithmeticTest.test_an_overlap_is_named_time_ambiguity_and_never_continuity test_day_life_runtime_coverage.CoverageArithmeticTest.test_an_unmeasured_unclosed_row_is_not_counted_twice`
  → `Ran 2 tests ... OK`；同一 2 条 ID 的 confirm-run（`PYTHONDONTWRITEBYTECODE=1`，cwd=`tests/`）实测
  `EXITCODE=0`。两段输出都在唯一日志
  `$env:PI_SCRATCH_DIR\run6-overlap-and-doublecount-20261018-200617.log`（confirm-run 段带 `EXITCODE=0` 行）；
  红证 `$env:PI_SCRATCH_DIR\day-life-coverage-parent-boundaries-gen1.log` 保留未动（旧行为 `OVERLAP_IGNORED` + `continuous=True`）。
- 反例都有断言：两个已关闭进程 → `covered 5400 ≠ wall 12600`、gap 7200、`continuous=False`；
  未闭合行尾部 3000 进 unknown；wall 回拨 → `wall_span=0` + `wall_rollback`；无测量 → unknown 1200 且
  covered 0；旧 summary 的 90000 秒 → unknown 90000 且 `soak_24h` 仍是 `SOAK_RUNNING_24H`；
  poll 180 / duration 60 → 只睡 60 秒；无证据 ACTED → confirmed 0 / unknown 1；无 ledger 时
  `total_requests = 4 + (2+1)`、账本读不出 → `None`；
  **重叠**（a 01:00–02:00 / b 00:30–01:30，各 3600）→ `covered=7200` 不减、`wall=1800`、`overlap_seconds=5400`、
  `gaps=[OVERLAP/ambiguous]`、`wall_rollback=False`、`covered_exceeds_wall_span=True`、`time_ambiguity=True`、
  `continuous=False`、`soak_24h=SOAK_RUNNING_24H`；**无测量未闭合行 + 新进程** → `unknown_seconds=3600`，
  条目 `[1200 NO_MONOTONIC_MEASUREMENT, 2400 UNCLOSED_INTERVAL]`，条目和 = 总数（不再 4800/6000）。
- 未做：真实 24h 常驻、真实模型调用、真实 resident 启动、`py_compile`、全树回归、全树 SHA、
  物理删除与 `TemporaryDirectory` 清理（夹具目录全部保留）。
