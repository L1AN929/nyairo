# W03 使用说明：前台常驻旅程推进器（run / once / status / pause / resume）

- 代码：`chiyo_bundle/world_runner.py`（推进器 + CLI）、`chiyo_bundle/world_control.py`（持久暂停状态）、
  入口薄包装 `scripts/life_heartbeat.py`（默认子命令 `once`，兼容 W02 旧入口）。
- 它只做一件事：**在没有新聊天输入时，持续调用既有旅程引擎，让用户已经接受的旅程真实到达并续行**。
  它**不生成目标、不调用 LLM、不装服务、不写系统配置、不联网**。
- 平台：需要 POSIX 文件锁（`fcntl`）与时区库 —— Linux / WSL 可用；原生 Windows 上
  `run` 会以 `LEASE_UNAVAILABLE` 拒绝（`status` 可只读运行，但 lease 证据为 `unknown`）。

## 1. 显式装配（没有隐式默认）

| 输入 | 说明 |
| --- | --- |
| `--world-home <绝对路径>` | **必填**。当前世界的私有状态目录（含 `data/world/world_state.json`）。必须已存在、已初始化，且在源码树之外。 |
| `--world-package <绝对路径>` | 活动世界包目录（等价于 `CHIYO_WORLD_PACKAGE`）。省略时按 `--env` 或空（内建汐见市）。 |
| `--env KEY=VALUE` | 额外环境覆盖，可重复。行为特性（如 `CHIYO_WORLD_LIVING_SKELETON_ENABLED=1`、`CHIYO_WORLD_TRAVEL_TIME_ENABLED=1`）**必须显式给**。 |

环境装配规则（`world_runner.build_environment`）：

1. 运行环境**不继承**进程里的世界配置：`CHIYO_WORLD_HOME` / `HERMES_HOME` / `CHIYO_WORLD_PACKAGE` /
   `CHIYO_WORLD_ID` 一律先剔除，再由显式参数填回。
2. `--env CHIYO_WORLD_HOME=<别处>` 或 `--env HERMES_HOME=<别处>` → `ENVIRONMENT_IDENTITY_CONFLICT`（拒绝，零创建）。
3. `--world-package` 与 `--env CHIYO_WORLD_PACKAGE=` 冲突 → `ENVIRONMENT_PACKAGE_CONFLICT`。
4. `CHIYO_WORLD_ID` 不靠猜：先由 `world_home/data/world/world_state.json` 与活动世界包交叉校验
   （`WORLD_HOME_IDENTITY_MISMATCH`：world_id 或 timezone 不一致即拒绝）。
5. 错误路径**先拒绝再动手**：缺目录/非目录/未初始化/在源码树内/包不可解析，全部在创建任何
   目录、锁、状态之前返回退出码 2。
6. 运行中不切 profile / world_home：一个进程只服务一个 `world_home`。

示例（隔离 profile 占位符，**不要**指向真实服务器）：

```bash
export W=/srv/nyairo/profiles/<profile>/world-home          # 占位符
export P=/srv/nyairo/profiles/<profile>/world-package       # 占位符
GATES="--env CHIYO_WORLD_LIVING_SKELETON_ENABLED=1 --env CHIYO_WORLD_TRAVEL_TIME_ENABLED=1"

python3 scripts/life_heartbeat.py status --world-home "$W" $GATES
python3 scripts/life_heartbeat.py once   --world-home "$W" --world-package "$P" $GATES
python3 scripts/life_heartbeat.py run    --world-home "$W" --world-package "$P" $GATES --interval 5
python3 scripts/life_heartbeat.py pause  --world-home "$W" --world-package "$P" --reason maintenance
python3 scripts/life_heartbeat.py resume --world-home "$W" --world-package "$P"
```

## 2. 命令语义

| 命令 | 副作用 | 说明 |
| --- | --- | --- |
| `run` | 有 | 前台常驻：monotonic 调度 + UTC 真实时间推进；持排它 run lease。**拒绝 `--now`**（`FIXED_CLOCK_NOT_ALLOWED`）。 |
| `once` | 有 | 推进一次（结算到期段 + 至多启动一个新段）。保留 `--now`（须带时区，测试用）。 |
| `status` | **无** | 纯只读投影：零创建、零写入、不结算到达、不写 mtime。缺目录/无 runner/坏记录都如实分类。 |
| `pause` / `resume` | 有 | 写持久暂停位（原子、版本化）。 |
| （无子命令） | 有 | 等价 `once`，兼容 W02 旧入口。 |

常用参数：`--interval`（秒，默认 5，下限 0.05，拒绝 `nan`/`inf`/负数/0.001）、`--max-ticks`（测试用，
正数）、`--settle-only`（只结算已出发段，不发起下一段）、`--reason`（pause/resume 标记）。

## 3. 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功（含 `paused` 状态：这是被请求的正常状态，不是错误） |
| 1 | 拒绝/降级：`degraded`（图变更、坏 travel 记录…）、`control_blocked`（控制状态损坏） |
| 2 | 用法/校验错误（**零创建、零移动**）：坏路径、身份冲突、坏 interval、`run --now`、坏 `--now` |
| 3 | 严重运行状态：`JOURNEY_STATE_CORRUPT`（旅程账本损坏，fail closed，不"修复"不删除） |
| 4 | run lease 已被另一个进程持有（快速失败，绝不等待成第二个写者） |

错误输出走 **stderr**（`{"ok":false,"reason_code":…}`），stdout 只承载命令结果 JSON。

## 4. 锁、并发与单实例

全局唯一锁序（不许出现反向获取）：

```
world_journey.json.lock  →  world_state.json.move.lock  →  world_travel_delivery.json.lock
```

- **run lease**（`<world_home>/data/world/world_runner.lock`）只保证常驻循环唯一，
  与上面三把锁**完全独立、不嵌套**：`status`/`pause`/`resume` 不获取 lease，
  所以不会因为 runner 正在跑而阻塞或死锁；手动 `once` 与模型工具仍靠 journey 锁协调。
- 第二个 `run` 进程立即以退出码 4 失败（`LOCK_EX|LOCK_NB`，不等待）。
- 进程死亡（含 `kill -9`）时内核自动释放 lease；**不靠删除 PID 文件破锁**，
  PID 只作展示（`pid_is_liveness_proof: false`）。
- 停止/退出清理在**持有 lease 期间**完成，因此不会覆盖后来重启进程的记录。

## 5. 持久暂停（本单核心）

`pause` 的定义：**不再接受新的移动、不再启动新段**；已经真正出发的那一段仍按真实时间到达并确认，
下一段留待 `resume`。不冻结物理在途、不倒退位置、不重置 `expected_arrival_at`。

- 闸门生效点在**引擎提交边界内**（`world_journey._step` 的 `not_started` 分支 + `accept_journey`），
  与 `pause` 的写入在同一把 journey 锁内 → 线性化点明确：**`pause` 返回成功之后不会再启动新段**。
  因此模型工具、`accept_journey`、`advance_journey`、`once`、即时模式（关闭计时）都绕不过它。
- 即时模式（`CHIYO_WORLD_TRAVEL_TIME_ENABLED=0`）的 `world_move` 逐段提交：**每一段都在同一把
  journey 锁内重新读取闸门后才调用 owner**，锁序仍是 journey → world → travel。所以
  `pause` 至多等待"当前这一段"提交结束，确认后不再提交下一段；多段路线中途暂停时，
  返回的是最后确认位置与已提交路线（`committed_route`），**不会**声称整条旅程到达。
- 状态写在 `<world_home>/data/world/world_control.json`（私有、`world.control.v1`、原子替换、`0600`），
  **跨 run 重启保持**；`resume` 只解除"阻止新段"，不自动新建旅程、不重规划、不重走已确认段。
- 控制状态**损坏/不可读 = fail closed**（`control_blocked`），绝不默认"未暂停"；
  "没有暂停记录"（absent）与"损坏"（corrupt）在 status 里分开报告。
- `pause` 本身不写旅程账本、不标记 blocked：暂停不是失败，可恢复。
- `--settle-only`（或 `advance_journey(settle_only=True)`）是显式的一次性"只结算"模式：
  结算并确认已出发段，但绝不发起下一段；它**不能**用来绕过持久暂停。
- 禁用行为特性（如 `CHIYO_WORLD_TRAVEL_TIME_ENABLED=0`）与暂停是**两个不同状态**：
  前者是 `feature_disabled`（非终态、可恢复），status 分开报告。

## 6. `status` 输出（只读）

```jsonc
{
  "kind": "shiomi_world_runner_status",
  "world_home": "/srv/…/world-home",
  "checked_at": "2026-10-14T03:00:00Z",
  "runner": {
    "classification": "running|stale|stopped|crashed|not_running|unknown",
    "health": "healthy|degraded|stale|stopped|crashed|unknown|not_running",
    "liveness": "held|free|unknown",          // 在已存在的锁文件上做无创建探测
    "liveness_evidence": "...", "record_state": "absent|ok|corrupt",
    "stale": false, "pid": 1234, "pid_is_liveness_proof": false,
    "last_tick_at": "…", "last_tick_status": "ok|idle|paused|control_blocked|feature_disabled|degraded|severe",
    "last_tick_reason": "…", "consecutive_degraded": 0, "next_tick_at": "…",
    "stop_reason": "SIGTERM|MAX_TICKS|SEVERE_RUNTIME_STATE|STOP_REQUESTED"
  },
  "control": { "state": "absent|ok|corrupt", "paused": false, "revision": 3, "paused_at": null, "resumed_at": "…" },
  "journey": { "status": "…", "target_location_id": "…", "current_segment": 1, "location_id": "…", "revision": 7 },
  "world": { "world_home_state": "initialized|missing|…", "world_id": "…", "graph_fingerprint": "…" },
  "feature_gates": { "living_skeleton": true, "travel_time": true, "delivery_time": false },
  "reason_codes": []
}
```

判读要点：

- **健康与旅程结果分开**：`runner.health` 只描述推进器本身；旅程的 `completed`/`blocked`/`unknown`
  在 `journey` 里。
- `classification` 的判据是"记录 + 真实锁探测"：`running`（记录 running 且 lease 被持有）、
  `stale`（lease 被持有但 `last_tick_at` 超过 `stale_after_seconds`）、`stopped`（正常退出记录）、
  `crashed`（记录仍写 running 但 lease 已释放 —— 进程死了）、`not_running`（无记录）、
  `unknown`（记录损坏，`RUNNER_STATE_UNREADABLE`）。
- **PID 不是存活证据**：PID 文件/进程号复用都不算；`liveness` 来自真正的锁探测。
- `control_blocked` 会让 `health` 变 `degraded`：坏控制状态时不该报告健康。
- `status` 永不创建目录/锁/状态、不写 mtime、不调用 `realize`/`read_move_state`、不推进。
  不同字段可能读到不同时刻（各自的 `revision`/`checked_at` 是权威），不承诺原子快照一致性。
- 输出经过脱敏：不含完整 environment、密钥、真实配置原文或文件路径细节。

## 7. 信号、重启与故障语义

- `SIGTERM`/`SIGINT`：结束等待（等待被切成 ≤0.25s 的片，信号可及时生效）、退出当前安全步骤、
  写本进程状态（`status=stopped`、`stop_reason=SIGTERM`）并释放 lease，退出码 0。
  **不调用 `kill`/`taskkill` 去停别人的 PID**；停止只靠 Ctrl+C 或上级 supervisor 的信号。
- 崩溃（`kill -9`、断电）：记录会停留在 `running`，但 lease 已被内核释放 → status 报 `crashed`。
  重启直接再 `run` 即可（不删锁文件、不手工清状态）。
- 分类与退避：暂时 IO 错误、未知旅程、图变更 → `degraded`（退出码 1，常驻循环继续，
  等待按 `interval×2^n` 有限增长、上限 60s，不紧密重试、不刷日志）；旅程账本损坏 →
  `severe`（`JOURNEY_STATE_CORRUPT`，退出码 3，停止并**保留原字节**，不"修复"不删除）。
- 目标永不自动丢弃、不随机新建旅程、不重建坏记录；`blocked` 旅程只有低层显式
  `accept_journey` 能归档并换新目标（工具会明确说明，不自动替换）。

## 8. 交给 supervisor 托管（**本单未安装**）

W03 只交付前台进程，**没有**安装 systemd/计划任务/daemon，也没有注册自启动。
如果以后要托管，可用如下命令（示例占位符，**未安装、未启用**）：

```ini
# /etc/systemd/system/<profile>-world-runner.service  —— 本单未创建、未启用
[Service]
ExecStart=/usr/bin/python3 /srv/nyairo/app/scripts/life_heartbeat.py run \
  --world-home /srv/nyairo/profiles/<profile>/world-home \
  --world-package /srv/nyairo/profiles/<profile>/world-package \
  --env CHIYO_WORLD_LIVING_SKELETON_ENABLED=1 \
  --env CHIYO_WORLD_TRAVEL_TIME_ENABLED=1 \
  --interval 5
Restart=on-failure
```

## 9. 已知边界

- 只有 POSIX 平台能跑常驻循环；Windows 原生只能 `status` 只读。
- 常驻循环的自动化测试用注入 clock/sleeper（`run_forever(clock=…, sleeper=…, utcnow=…)`），
  **没有**做过几十分钟 wall-clock 的真实长时 soak。
- 它不生成目标：没有已接受的旅程时 `tick` 就是 `idle`（暂停时报告 `paused`）。
- 暂停只拦"新段"，不会撤销已在途的物理移动；位置与 `expected_arrival_at` 一律由 owner 投影决定。
