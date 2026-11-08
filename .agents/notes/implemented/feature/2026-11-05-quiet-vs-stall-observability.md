# Agent Note: 让"她安静"和"她卡住了"能从 status 上分开

Status: implemented

## Problem

验收要求 E11 是"无事安静日与死进程/卡队列可区分，不补写剧情"。只读核查的结论比预想的更难看：

- 能区分"安静"和"进程已崩"——supervisor 一秒内发现子进程退出就写 `stop_reason: RESIDENT_EXITED`。
- **不能**区分"安静"和"进程卡在某个 tick 里"。卡住时 `last_tick_at`、覆盖账本心跳、`resident_journal`
  全停，但 supervisor 的 `fastday/clock_ticks.jsonl` 照常每秒涨，`fast_day status` 仍报
  `child_alive: True`（`_pid_alive` 只做 `os.kill(pid, 0)`，不校验启动时间）。没有超时检测。
- 最讽刺的一点：那份每秒心跳，**全仓只有测试在读**，生产代码一处都没看。健康检查模块算出了
  tick 距今秒数，又硬写 `no_timeout_rule: True` 拒绝下结论。
- 记录本身没塌：`resident_journal.jsonl` 每行都有具名 `due`，四类分开——`NOT_DUE/silence`、
  `QUIET/silence`（带原时间戳）、`NO_ACTION/choice`、`BLOCKED/blocked`（具名原因如 `GATE_OFF`、
  `ROUND_BUDGET_EXHAUSTED`、`MODEL_TIMEOUT`）。塌在汇总层：`resident_summary.counters` 是 wake 级，
  w 批 16 个决策窗把 2896 个 tick 吃成一个粗计数，而 `status` 只展示这份计数。

## Decision

三处，只做"看得见"，不做"自动处理"：

1. `resident_summary` 新增 `last_due`，由 `_last_due_view()` 从 `self.last_tick["due"]` 带出
   `at / tick / kind / status / status_class / outcome / reason / detail / trigger_kind`。
   只加字段，`last_tick_at` 等既有字段语义未动，`resident_summary.json` 无 schema 版本，没撞校验。
2. `fast_day status` 增加 `stall` 块：`tick_lag_seconds`（读 `clock_ticks.jsonl` 尾块取末行
   `real_at`，与 `last_tick_at` 相减）、心跳时刻与序号、`judgement`。可传 `--stall-threshold`
   得到 `WITHIN` / `EXCEEDED`；非正值报 `STALL_THRESHOLD_INVALID`。**默认只标注数字，不判死**，
   因为一次正常的模型调用（默认 30 秒超时）就会拉大这个差。
3. `runtime_health_review` 新增 `stall_judgement()` 与四个具名结论；不传阈值时输出与改之前
   逐字段相同，传了才把 `no_timeout_rule` 置 False 并多出 `progress.stall`。

## Alternatives considered

- **引入自动超时判死并重启。** 最强立论：真正把"卡住"变成可自愈。否决原因：区分"卡住"和
  "正在跑一次很长的模型调用"需要更多判据，现在只有 tick 间隔，误杀一次正常运行的代价远大于
  多看一眼日志。先把数字摆出来，让看的人判断。
- **把 tick 级 `due` 全量投影进 summary。** 最强立论：信息最全。否决原因：一份运行有几千行 tick，
  全塞进汇总等于让 summary 变成日志，而且会让 summary 每秒重写一大块。只留最近一次。

## Consequences

- 现在能从 `status` 一眼看出"这一轮她为什么没动"，以及"tick 落后心跳多少秒"。
- 卡住时外部会看到：`tick_lag_seconds` 持续增大而 `last_tick_at` 不动——有了可判断的入口，
  但仍需人判断，程序不自动判死。
- 旧的 summary 没有 `last_due` 时会报 `null`，不补写历史。
- 父级复跑：`test_fastday_runtime` 40 条、`test_resident_projection_counts` +
  `test_runtime_health_distinction` 32 条，全绿。
- 未做：卡队列检测（AR-0 提交尝试加 `started_at` 与超时判据）留作另一单；`--stall-threshold`
  尚未接进 `runtime_health_cli`。
