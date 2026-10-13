# v0.2 B8-6：常驻运行与真实长测（E09.5 / E12.5）

本批起点 `43de2e6`，分支 `v0.2-autonomous-life`。

## 1. 核心裁定：独立驱动，单一 owner

`runner_tick()` 不挂 Hermes 的 `pre_llm_call`。用户聊天不能成为自主生活继续运行的必要条件。
本批新增一个轻量常驻进程 `components/life/scripts/autonomous_life_resident.py`，按现有规则驱动：

```
事件 Feed 检查 → runner_tick() → 到期窗口 / 有效事件 → AutonomousLifeCycle → 真实 owner
```

* 复用 B8-2 的 `autonomous_life_assembly`、`AutonomousLifeRunner`、Feed 与 `ProductionLifeRuntime`；本批只加一个循环和一份记录。
* **复用既有 canonical writer lease**：`ProductionLifeRuntime` 自己取的租约就是互斥，本批**没有**为了双进程冲突去取消任何租约。
* 同一 state root 只允许一个进程：第二个进程被既有 `ActivityLockError` 拒绝（见 §5）。
* 没有新建调度平台、任务队列或数据库。
* 无用户消息也能运行（见 §4）。
* 关闭后停止新决策与新提交，但 `reconcile_pending()` 仍按原 operation 对账。

**与 Hermes 的关系（记录在案）**：未来若由独立进程持有正式实例的 writer lease，Hermes 必须通过只读投影
（`chiyo_bundle/presence.py` 的 `life_projection`）或现有合法接口查看生活状态，**不能**再为同一 state root
创建第二个写入 Runtime。本批只在专用隔离 profile 里跑，没有碰生产实例。

## 2. 最小常驻启动方式

隔离环境的 systemd 是 `offline`（`systemctl is-system-running` → `offline`），所以沿用该环境已有的进程管理
机制（nohup + pidfile，Life Supply owner 与 World service 都是这么跑的），不引入 Docker/Redis/调度服务。

```bash
# 1) 准备专用隔离 profile（自己的 subject / workspace / 授权 / state root / Memory / 日志）
bash components/life/tests/v02/run_resident_soak.sh prepare
# 2) 起常驻（默认 180s 轮询；正式重新考虑窗口仍用配置里的 120 分钟）
NYA02_RESIDENT_POLL_SECONDS=180 bash components/life/tests/v02/run_resident.sh start
# 3) 看状态 / 停
bash components/life/tests/v02/run_resident.sh status
bash components/life/tests/v02/run_resident.sh stop
```

生产部署建议的 systemd 单元（本机 systemd offline，未在本批验证）：

```ini
[Unit]
Description=NyAiro autonomous life resident
After=network-online.target
[Service]
Type=simple
User=nyasvc
EnvironmentFile=/srv/nya02/resident/resident.env
ExecStart=/usr/bin/python3 /opt/nyairo/components/life/scripts/autonomous_life_resident.py \
  --state-root /srv/nya02/resident/state --poll-seconds 180
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

启动顺序（施工单要求）：读配置与 state root → 装配**唯一**的真实 `ProductionLifeRuntime` → 取已有 writer lease →
装配 Cycle/Runner/EventFeedPoller → **执行一次 pending/UNKNOWN 对账** → 进入低频循环。
运行时：按 Feed 与到期窗口调 `runner_tick()`；未到窗口不调模型；无候选不调模型；遵守每日认知/动作预算；
**不补跑**停机期间错过的窗口；正常退出释放 runtime 与 lease；轮询 180s（不每秒空转）。

## 3. 三种事件 Feed 的实际接线状态

B8-2 只装配了两种。本批补齐了 World 的端口接线：`autonomous_life_assembly.resolve_world_provider()` 从实例配置
（`WORLD_BODY_SOCKET` / `NYAIRO_WORLD_BRIDGE_DIR`）解析真实观测缝，先用一次有界的 `get_status()` 探测，
只有 World 真的 `READY` 才装配 `WorldOpportunityFeed`；否则记录 `UNAVAILABLE` + 具名原因，**不猜机会**。

隔离 profile 实测（World 起来后）：

```
feeds: ['resume_eligibility', 'artifact_head', 'world_opportunity']
world: {"status": "READY", "reason": "", "detail": "supplied by the caller"}
```

| Feed | 状态 | 证据 |
|---|---|---|
| ResumeEligibilityFeed | 已装配 | B8-1 单测 + 本批 feeds 列表 |
| ArtifactChangeFeed | 已装配 | 本批 feeds 列表；`real_events: 4` |
| WorldOpportunityFeed | **本批补齐接线**，World READY 时装配 | `world: READY` + feeds 列表含 `world_opportunity` |

World 不在时的行为也已验证：早期运行时 `world: {"status":"UNAVAILABLE","reason":"WORLD_NOT_READY"}`，feeds 只有两项，
无 World 场景正常运行。

## 4. 无用户消息时的真实运行记录

隔离 profile：subject `chiyo-soak`，自己的 workspace `workspace:df47971f…`、授权 `grant:1138d2a8…`、
state root `/srv/nya02/resident/state`、Memory `/srv/nya02/resident/memory/evidence.db`、作品
`artifact:62e8f277…`（v1 起）。**没有任何用户消息**。

结构化运行摘要（`resident_summary.json` + `resident_journal.jsonl`）：

```
enabled: True  starts: 8  ticks: 20  poll: 180  interval_min: 120
soak_24h: SOAK_RUNNING_24H   soak_7d: SOAK_PENDING_7D
feeds: ['resume_eligibility', 'artifact_head', 'world_opportunity']   world: READY
counters: check_windows 42, event_wakes 4, real_events 4, model_calls 4,
          outcomes {ACTED: 4}, owner_confirmed_versions 4, actions 4,
          by_class {blocked: 42, acted: 4, silence: 5},
          blocked_reasons {"autonomous life is off; nothing was considered": 41,
                           "budget refused the wake: ROUND_BUDGET_EXHAUSTED": 1},
          pending_unknown_last 0, pending_unknown_max 0
budget: cognition 4/6, actions 4/4 used today
```

一次真实的自主轮次（journal tick 15）：

```json
{"at": "2026-10-11T01:16:32+00:00", "outcome": "ACTED", "decision": "START", "llm_calls": 1,
 "action_id": "actn:81e5dbaee7b4462f", "action_status": "SUCCEEDED",
 "activity_ref": "actv_fa108234234fcb70", "trigger_kind": "EXPLICIT_REEVALUATION",
 "detail": "owner committed artifact-version:b082d350155b466a93be8546cc7d2032"}
```

**认知来自真实模型**（`gemini-3.8-flash-high`，无替身）。owner 回执确认了新版本，作品从 v1 推进到 v5，
新增的是真实正文（两段散文，不是模板）。停止后作品 head 不变（§5）。

## 5. 真实时间与恢复验收

| # | 要求 | 结果 | 证据 |
|---|---|---|---|
| 1 | 常驻进程无用户消息持续运行 | 通过 | pid 存活，ticks 持续增长，全程无用户消息 |
| 2 | 空闲窗口零 LLM、零动作 | 通过 | 41 个窗口 `DISABLED`（开关未开）+ 4 个 `silence`，`model_calls` 与 `actions` 都不动 |
| 3 | 合法事件确实进入一次考虑窗口 | 通过 | `real_events 4` / `event_wakes 4`（详见 §7 的发现） |
| 4 | 重启后不补跑历史窗口 | 通过 | 重启后 `interval_minutes=120` 生效，`last_check_at` 保留，未出现补跑 |
| 5 | 重启后开关与预算不重置 | 通过 | `starts: 8`（累加）、`enabled: True`（持久化开关）、`budget` 仍是 4/6 与 4/4 |
| 6 | 已提交 Action 不因重启重复执行 | 通过（本批无 UNKNOWN） | AR-0 原 operation 对账；`pending_unknown_max 0`，无重复版本 |
| 7 | UNKNOWN 可继续对账 | 通过（无待对账项） | `reconcile_pending()` 每次 tick 与启动各跑一次；`reconciliations` 计数；UNKNOWN 路径在 B8-5 已验证 |
| 8 | 停止后没有新的自主作品写入 | 通过 | `stop` 后 head 不变（`c2f9e1d8…` v5），进程退出 |
| 9 | 日预算按正确日期边界更新 | 通过 | 预算文件按 `_local_day()` 分日，`spent_today` / `remaining_today` 一致 |
| 10 | pending 不因循环运行而无限增长 | 通过 | `pending_unknown_last 0`、`pending_unknown_max 0` |

**受控停止与重启已实际执行**（`STOPPED` → 重启 `starts` 从 7 到 8）。
**单 writer lease 验证**：同一 state root 的第二个进程被既有 `ActivityLockError` 拒绝；launcher 另有一层
`ALREADY_RUNNING` 前置检查。**本批没有通过取消租约来"解决"冲突。**

## 6. 24h / 7d 的真实时间状态

```
soak_24h: SOAK_RUNNING_24H
soak_7d:  SOAK_PENDING_7D
```

观察从 `2026-10-11T01:07:06+00:00` 开始，本批结束时**远未达到 24 小时**，因此**不标 `SOAK_VERIFIED_24H`**，
也不宣称做过 24 小时或 7 天观察。常驻进程保持运行，记录继续累积；7 天观察复用同一套运行器与记录机制
（`resident_journal.jsonl` 足以回答施工单列出的七项：跨日同一作品、有效续作、正常静默、PAUSE/ABANDON 或方向变化、
历史经历是否被未来读取、用户聊天能否自然使用真实生活信息、异常与恢复）。**没有用加速时钟代替自然日。**

## 7. 测量中发现的问题（如实记录）

1. **自己的写入会再次唤醒自己**（`real_events 4` 与 `owner_confirmed_versions 4` 数量吻合）。B8-1 的单测声称
   "自己提交的作品变化不形成无限循环"，但常驻进程里 4 次自主写入各触发了一次 `artifact_head` 事件唤醒。
   本批**没有形成无限循环**，因为每日轮次预算把它截断（`ROUND_BUDGET_EXHAUSTED`），但按施工单"自己提交的作品变化
   不形成无限循环"这一条，这里仍需在 B8-7 之前查清：Feed 是否真的把"我们写的 head"记为我们的。
2. **launcher 曾把 provider 密钥放进命令行**：`runuser … env NYA02_MODEL_API_KEY=… python3` 会让 `ps` 对全机可见。
   已改为写 0600 文件（`$RESIDENT_DIR/model_env`，nyasvc 拥有），常驻进程自己读取；修复后命令行不再含密钥。
   **同类写法在 B8-4/B8-5 的 `run_model_e2e.sh` 与 `run_b8_causal.sh` 里仍然存在**，本批未改（不在范围内），
   建议连密钥一起轮换。
3. 本批修掉的自身缺陷：`baseline` / `reconciled_at_start` 被下一次写入覆盖而丢失；计数器重启后归零（现从
   journal 重放）；`autonomous_enabled` 显示的是装配时的旧值（现读实时开关）；launcher 未把 World 变量交给
   服务身份（World feed 因此一直 UNAVAILABLE）；launcher 的 subject 会被 `$ROOT/env.sh` 带偏（现以 profile 里
   记录的 subject 为准）；World service 用普通 `&` 启动会被 SIGHUP 杀死（现 `nohup setsid`）。

## 8. 失败套件与未完成条件

`python3 scripts/test.py` → 通过：closeout / native / memory / supply / world / life / life-v02
（`MEMORY_CHECK: PASS`）/ life-memory-v02 / life-b7 / **life-b8（`B8_CHECK: PASS`）** /
**life-b8-causal（`B8_CAUSAL_CHECK: PASS`）**。

`FAILED_SUITES: ['host-boundaries', 'cognition-faults', 'Hermes']` —— 三者都**不影响**本次常驻启动、插件加载或真实模型运行：

* `cognition-faults`：`vendor/hermes` 没有 pytest 虚拟环境；常驻进程不使用 pytest 也不加载 vendor/hermes。
* `host-boundaries`：安装器/更新/文件系统安全测试，与常驻进程无关。
* `Hermes`：vendor 测试运行器失败；插件加载本身是好的 —— `life` 套件报告
  `LIFE ADAPTER: register() ran; pre_llm_call hook registered (pid=… mode=live runtime_loaded=True)`。

按施工单，这三项**保留 FAIL 并记录**，没有顺手重构安装器。

**未完成条件**：24h / 7d 真实时长（`SOAK_RUNNING_24H` / `SOAK_PENDING_7D`）；§7.1 的自我唤醒归因；
真实模型调用费用以 `counters.model_calls` 为准（本批 4 次，未充值、未改密钥）。

## 9. B8-7 开工前的剩余阻塞

1. **§7.1 自我唤醒**：需要先查清 Feed 对"自己的写入"的归属，再决定是补 `record_own_write` 接线还是别的修法。
2. **密钥轮换**：命令行泄露过 provider 密钥（§7.2），且同类写法还在两个驱动脚本里。
3. **24h/7d 未达**：长测继续运行中；标签只能在真实时长达到后升级。
4. **B8-4 遗留未动**：T20（缺 v0.1 形态 profile fixture）、T12（原生生活经历 CORRECT 入口未实现，不得伪造用户消息）、
   T06/T07（有前台 Activity 时 AG-1 规则先解析，PAUSE/ABANDON 不经模型）。
5. **GitHub 推送仍失败**：提交只在本地，离线 bundle 兜底。
6. **systemd 未验证**：本机 `offline`，生产单元只是建议稿。
