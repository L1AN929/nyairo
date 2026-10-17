# 一天自主生活运行手册（W05）

命令一律**显式**给参数；没有任何隐式默认，也不会自己找 profile。占位符写成 `<...>`，不要照抄。

## 0. 前提

- Linux/WSL：需要 POSIX 文件锁（`fcntl`）与 `AF_UNIX`；原生 Windows 只能 `status`/`report`。
- 需要**活着的 Life Supply owner**（一个 Unix socket，同时承担 governance 与 supply 两面）：
  `lifesupply.service.life_supply.LifeSupplyService` + `LifeSupplySocketServer`，
  或部署版 `scripts/supply_service.py`。写闸门（`REAL_WORKSPACE_PROVISION` / `REAL_ARTIFACT_ACTION`
  或 `FeatureSwitches`）属于**服务进程**，不属于本入口。
- 世界侧：`WORLD_BODY_INTENT_MUTATION_GATE=canonical` 必须由**运行进程的环境**提供
  （World bridge 自己的契约；它由 Life 进程里的 Intent Executor 读，关掉时 action space 里
  没有 MOVE 候选，她就哪儿也去不了）。真实部署还需要 World 服务自己的
  `gateway.sock` 与 `NYAIRO_WORLD_BRIDGE_DIR`；两者都可以用 `run --env KEY=VALUE` 逐次传入。
  W05R 起，计时 MOVE 走**世界自己的正式授权入口**（Intent Executor 铸一次性授权 →
  `gateway.sock` 消费 → Resolver 提交），不再由旅程引擎直接调 owner 提交函数。
- 模型：`<instance>/model_env`（`0600`）放 `NYA02_MODEL_PROVIDER / NYA02_MODEL_NAME /
  NYA02_MODEL_BASE_URL / NYA02_MODEL_API_KEY`。密钥**不进命令行**（`ps` 可见）、不进仓库、不进日志。
- 独立实例根：`<instance>` 必须与真实 profile、真实 state root 分开。

## 1. 精确命令

```bash
export PY=/usr/bin/python3
export APP=<repo>/components/life/scripts/one_day_life.py
export INSTANCE=/srv/nyairo/profiles/<profile>/one-day
export SUPPLY_SOCK=/srv/nyairo/profiles/<profile>/sock/life-supply.sock
export GOV_SOCK=$SUPPLY_SOCK            # owner 模块的 socket 同时承担两面
export WORLD_PACKAGE=/srv/nyairo/profiles/<profile>/world-package
export WORLD_BODY_INTENT_MUTATION_GATE=canonical     # 进程环境，必须有
export WORLD_BODY_SOCKET=/srv/nyairo/profiles/<profile>/world.sock   # 真实部署才需要
export NYAIRO_WORLD_BRIDGE_DIR=<repo>/components/world/service/bridge

# 1) 准备隔离实例（走 owner 自己的命令：发 grant → 建 workspace → 建作品(operator 种子) → 建世界）
"$PY" "$APP" prepare --instance "$INSTANCE" --supply-socket "$SUPPLY_SOCK" \
  --governance-socket "$GOV_SOCK" --subject <subject> --title "<作品标题>" \
  --world-package "$WORLD_PACKAGE"
# 拒绝覆盖：<instance>/instance.json 已存在即 INSTANCE_EXISTS（退出码 1，零改动）

# 2) 只读体检（不调模型、不打印密钥）
"$PY" "$APP" preflight --instance "$INSTANCE"
# ok=false 时看 checks.model / checks.world / checks.supply / checks.life.writer_exclusion

# 3) 前台运行（默认上限 86400s = 24h；每 1800s 一个检查窗）
"$PY" "$APP" run --instance "$INSTANCE" --duration-seconds 86400 --poll-seconds 1800 \
  --request-utc-day-max 96 --request-run-window-max 96 --run-id run:<profile>-<utcday> \
  --env WORLD_BODY_INTENT_MUTATION_GATE=canonical \
  --env WORLD_BODY_SOCKET="$WORLD_BODY_SOCKET" \
  --env NYAIRO_WORLD_BRIDGE_DIR="$NYAIRO_WORLD_BRIDGE_DIR"
#   --once        只跑一跳（演练用）
#   --max-ticks N 上限跳数（测试用）
#   --no-act      只考虑、不执行
#   --new-run-window
#                 显式开一个新的请求窗口；**普通重启不要加**，否则等于给自己重新发额度
# 4) 只读状态（零创建、零写入）
"$PY" "$APP" status --instance "$INSTANCE"

# 5) 持久暂停 / 恢复（只写生活侧的 life_pause.json）
"$PY" "$APP" pause  --instance "$INSTANCE" --reason maintenance
"$PY" "$APP" resume --instance "$INSTANCE"

# 6) 24h 报告
"$PY" "$APP" report --instance "$INSTANCE" --duration-seconds 86400
```

请求预算（W05R 起是硬上限，不是提示）：

- `prepare` 一定把两个上限写进实例：`CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY` 与
  `CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW`（默认各 96），以及
  `CHIYO_AUTONOMOUS_RUN_ID`（默认取实例目录名）。
- 决策、内容生成、以及**每一次重试**的实际 HTTP attempt，都在**发出前**原子持久预留；
  超时、崩溃、进程被杀都**不退还**（没有 release API）。
- 普通重启带着同一个 `CHIYO_AUTONOMOUS_RUN_ID` 继续同一个窗口；只有显式
  `--new-run-window` 才开新窗口。
- 账本损坏/不可读 = **全部拒绝**（fail closed），不是"还没花过"。
- `report.model.request_budget` 直接读这个账本（当日计数、历史、窗口累计、最近 token）。

## 2. 暂停 / 恢复 / 回滚

- **两个开关，互不误触**：`pause`/`resume` 只写 `<life state root>/life_pause.json`；
  世界自己的暂停是 `<world home>/data/world/world_control.json`（W04），要用
  `scripts/life_heartbeat.py pause|resume --world-home ...` 单独操作。`one_day_life.py resume`
  **不会**解除世界暂停，世界 `resume` 也**不会**解除生活暂停。
- 暂停语义：确认之后不再开始新决策、不再发起新外部动作；**已出发的段仍按真实时间到达并结算**
  （暂停不冻结物理），结算走 `settle_only=True`，不会启动下一段。
- 确认边界（W05R-R1 收紧）：`pause` 在**模型/内容请求还挂着**的时候也必须能立刻确认 ——
  确认点不再是一个包住整轮的锁，而是每个外部动作之前重新读位（请求启动前、决策提交后、
  内容请求之后、owner 调用之前），只有**已经开始的有限 owner 调用**会挡住 `pause`（它由
  owner 自己的超时封顶）。请求已经发出的那一轮可以结束，但它的结果不会再落到任何 owner 上：
  该轮报 `PAUSED_BY_OPERATOR`，并带 `pause_boundary.held_after` 说明停在哪一个边界。
- 世界活动收尾（W05R-R1）：计时 MOVE 的 Activity 现在带 LR-5 的完成合同，到达（由引擎自己的
  `tick -> settle_journey` 按世界观察判定）会让它合法 `COMPLETED` 并释放前台；在那之前，任何
  想再开一个前台活动的决定都会被拒绝（`CANNOT_START_WHILE_FOREGROUND_ACTIVE`），这是合同行为，
  不是可以"重试到成功"的临时错误。
- 回滚：`rm` 之前先列清单并得到确认。停一个实例的最小动作是 `resume` + 停进程 + 保留 `<instance>`；
  本入口**从不删除**任何东西。

## 3. 停止与故障

- `SIGTERM`/`SIGINT`：结束等待、退出当前安全步骤、写本进程记录、释放 canonical writer lease。
- 崩溃（`kill -9`/断电）：记录停在 `running`，但 lease 由内核释放 → 重启直接再 `run` 即可。
  停机期间的检查窗**不补跑**；报告会把这段算成 downtime。
- 第二个 writer：runtime 建不起来（`ActivityLockError`），入口返回退出码 **4**，不等待、不抢锁。
- 退出码：`0` 成功（含 paused 是被请求的正常状态）｜`1` 拒绝（含 preflight 未就绪、实例已存在）｜
  `2` 用法错误（零创建）｜`3` 运行被阻塞（runtime 起不来等）｜`4` 另一个 writer 持有 lease。
- 错误输出走 **stderr**，stdout 只放命令结果 JSON。

## 4. 看真实作品与经历

- 作品：`status` / `report` 的 `work` 段给 head 版本号、行数与**真实内容摘录**（不只 hash）；
  owner 侧真源是 Supply 的 `read_artifact`/`read_head`。
- 经历：`report.window`（覆盖/停机/缺口）、`report.activity`（检查窗、NO_ACTION、DEFER、
- 经历：`report.window`（覆盖/停机/缺口）、`report.activity`（检查窗、NO_ACTION、DEFER、
  暂停窗、owner 确认结果数）、`report.model.requests` 与 `report.model.request_budget`
  （决策/内容/重试的持久计数与两个上限；token 只有供应商报了才有）、
  `report.travel`（旅程投影 + 最近历史）、`report.actions` / `pending_unknown`（AR-0 账本）。
- 报告**不会**自己铸 `PASS`：`PASS` 需要 有效观测 ≥ 目标时长 **且** 无停机缺口 **且** 至少一条
  owner 确认结果；零动作是合法的一天，但不自动证明自主链已验收。

## 5. 安全边界（本单未做）

- 未安装 systemd/计划任务，未注册自启动；下面的示例**未创建、未启用**：

```ini
# /etc/systemd/system/<profile>-one-day.service  —— 本单未创建
[Service]
Environment=WORLD_BODY_INTENT_MUTATION_GATE=canonical
EnvironmentFile=-/srv/nyairo/profiles/<profile>/one-day.env
ExecStart=/usr/bin/python3 <repo>/components/life/scripts/one_day_life.py run \
  --instance /srv/nyairo/profiles/<profile>/one-day --duration-seconds 86400 --poll-seconds 1800
Restart=on-failure
```

- 本手册不包含任何密钥；`model_env` 由 operator 以 `0600` 写好，入口只读不写、不打印。
- 真实 24h 启动前需要 operator 单独批准模型、预算与主机（见 `D:\NYAIRO-v0.3-24小时自主生活启动方案.md`）。