# 运行健康只读入口：安静、已停、待处理积压是三种事实

2026-10-29。对应 v2 总纲 14 §5 场景 11 的观测面。**只读**，不新增调度/看门狗/自动修复。

## 现在能用的命令

```bash
# 对 one-day 实例根（读 <root>/life/state）
python3 scripts/runtime_health_cli.py --instance /root/nyairo-day-...

# 直接给 Life state 根（隔离夹具用这个）
python3 scripts/runtime_health_cli.py --state-root <state>

# 完整 JSON
python3 scripts/runtime_health_cli.py --state-root <state> --json
```

退出码：`0` 三类确定事实（安静 / 安静有待处理 / 已停）；`3` 记录还写着 `running` 但进程已不在；
`4` 没有记录或进程身份无法核验。

## 现在能区分什么

| 结论 | 事实组合 | 它说明 |
|---|---|---|
| `ALIVE_QUIET` | 进程启动时间核验 MATCHED，未观测到 AR-0 待处理项 | 进程在，不是停机；若 queue=ABSENT，只表示没有动作账本，不证明队列为空 |
| `ALIVE_WITH_PENDING` | 进程 MATCHED，`open_submission_intents` 或 UNKNOWN 动作 > 0 | 在跑且有待处理；**没有**正式超时合同，不判卡死 |
| `STOPPED` | 记录没有声称 `running`，记录的进程在当前主机不存在 | 已停（正规停止） |
| `RECORD_STALE_PROCESS_ABSENT` | 记录仍写 `running: true`，但进程不在 | 旧记录残留，不是当前在线 |
| `UNKNOWN` | 缺 pid/host、跨主机、本平台无 `/proc` | 如实未知，不猜死活 |
| `NO_RECORD` | 该 state 根没有 `resident_summary.json` | 不编造在线/已停 |

同时打印：记录声称的 `running`、pid、起止时间、最近 tick 与距今秒数、coverage 五量
（covered / open tail / unknown / offline gap / continuous）、AR-0 未确认提交意图数、待对账动作数、
最近一次明确失败（`PRE_SUBMIT_FAILED` / `FAILED` / `REJECTED` 及原 `error_class`）、AR-0 revision 与
`updated_at`。

进程核验规则：读当前主机 `/proc/<pid>/stat` 的启动时间（第 22 字段 + btime），与记录里的
`(host, pid, started_at)` 比对；**pid 存在不算数**，启动时间匹配才算同一个进程。AR-0 队列直接读
`action_reality_state.json`，不调会重放 WAL 的 `load_state`。

## 不能判断什么

- **不判断卡死**：没有「多少秒没动 = 卡死」的阈值。长模型/owner 调用和正当 WAIT 都是合法的；
  队列有积压只报积压和最后进度。输出里不会出现 `STALLED`。
- **不判断个人经历**：进程在、心跳新、队列空只说明运行健康；不证明她在生活、做过动作或产生过作品。
- **跨主机/无 `/proc` 时进程状态恒为 UNKNOWN**：本机看不到另一台宿主的进程，不假支持。
- **队列只覆盖 AR-0 动作面**：没有动作账本时报 `ABSENT`（不冒充空队列）；其它 owner 的积压不在本入口。
- **不修复**：不取 Activity lease、不恢复 WAL、不重试/消费/重发、不写实例文件、不启动运行时。

## 已验证（隔离 fixture，0 模型请求）

`scripts/runtime_health_distinction_demo.py` 用 `offline_scripted + act=False` 的真实 resident
在 `mkdtemp` 根里走四段：安静（MATCHED）、SIGTERM 正规停止（STOPPED）、复制停止后记录并改回
`running` 的模拟崩溃残留（`RECORD_STALE_PROCESS_ABSENT`，exit 3）、以及通过 resident 自己 runtime 的
既有 AR-0 写口提交一条提交意图（受控 fixture adapter 在 fence 后抛错，不发送任何东西）后的
`ALIVE_WITH_PENDING`。`tests/test_runtime_health_distinction.py` 用伪 `/proc` 覆盖 pid 复用、
跨主机、无 host、无记录等负控。

未在真实长期实例上观测；不是 24h、不是 G1、不是完整场景 11。
