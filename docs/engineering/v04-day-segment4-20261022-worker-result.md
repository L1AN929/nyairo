# 同实例日常第四段 · worker 结果（2026-10-22）

只运行 `/root/nyairo-day-20261022` 的 `run:one-day` 一段 180 秒，未施工、未追加第二段。
所有命令经 WSL `/mnt/d/nyairo-rc1-body-backend`，环境只 source `/root/.nyairo-model.env`（不读取、不打印密钥）。

## 实际命令与退出码

1. `scripts/day_capability_probe.py status --root /root/nyairo-day-20261022`
   - exit `0`（`.agents/worker-runs/20261022-day-segment4-pre-run-status.exit`）
   - `probe_at=2026-10-22T14:10:42Z`，已过 14:09:46Z 自然窗口；`run_id=run:one-day`、
     账本 11、`lease=FREE`、`running_recorded=false`、`status=READY`，无身份/账本/writer 冲突。
   - 摘要里的 `*_max=12` 是上一段临时 cap 快照；run 报告确认 `configured_max=96`。
2. 旧报告副本：`/root/nyairo-day-20261022/probe/run-report.json` →
   `.agents/worker-runs/20261022-day-segment4-previous-run-report.json`。
3. `scripts/day_capability_probe.py run --root /root/nyairo-day-20261022 --duration-seconds 180 --poll-seconds 2 --max-new-requests 6`
   - exit `0`（`-segment.json/.log/.exit`）；本段 `14:10:59Z → 14:14:07Z`，180 秒，ticks 累计 284。
   - 自起本段 owner 子进程 supply 10353 / world 10354，收尾 `services_stopped.stopped=true`。
4. `scripts/day_capability_probe.py status --root /root/nyairo-day-20261022`
   - exit `0`；`READY`、账本 12、`lease=FREE`、`running_recorded=false`，
     下一自然窗口 `2026-10-22T14:41:02Z`。
5. `scripts/day_reading_material.py verify --root /root/nyairo-day-20261022`
   - exit `0`，`VERIFIED`；14 项 checks 全 true；ceiling 96/96、账本未重置、同 run、
     新材料可读且三处绑定一致、旧材料未改写；工具自起 supply 已停（exit -15）。

## 账本与预算

- 前后：11 → 12，`delta=1`；`same_run_window=true`、`ledger_reset=false`、未开新 run。
- 配置上限 96/96 未变；本段按 `min(96, 11+6)=17` 压低，`raised_any_ceiling=false`；
  本段请求 cap 要求 6，实际新增 1，未触及 17。
- worker原报“共同已用12/96、余84”漏算旧预检1次；父级更正：主实例12+旧预检1=共同13/96，余83。

## 本段决策与动作

- wakes：BLOCKED 1、NOT_DUE（安静）1；`acted=0`。
- `doing.status_word=BLOCKED`、`outcome=DECISION_FAILED`，阻塞原因：
  committed Decision `cand:a077fcadc6750011` 未指名任何已观测的 World target。
- `changed.actions=[]`；`owner_version_refs` 3 个 id 与上一段副本完全相同，无基线差异，
  不称本段新写任何版本。
- 模型面：ledger 增 1 次请求；进程内 `calls_this_segment=0` 与 ledger 增量口径不同，
  不能据此单方面断言调用类型；决策/提示/正文内容未读取、未输出。

## 未验证 / 保留

- 不证明真模型自然采用、不证明截断或任何失败模式消失；`understanding` 仍 UNKNOWN。
- Memory UNBOUND、24h NOT_RUN_BY_USER_SCOPE、EP-09 恢复 FAIL 均保留。
- 只跑这一段；未 prepare/reset/清 quiet/force wake/重放失败/改 cap 或配置；未跑浏览器/全库/旧绿套件。
- status 与 verify 由父级独立复跑，本报告只记录本 worker 实际执行结果。
