# v0.4 连续生活 C 侧交付结果：一条入口、真实循环语义、给操作者的有界 summary

> 三层表述（不合并）：
> **① 代码 + 真实 owner/runner/resident 的连续流程已验证**（同一真实手机实例上 4 段连续生活：
> 起 owner → 真循环 → 停 → 续跑 → 异常退出 → 取消；`Ran 16 tests ... OK`）。
> **② 模型是脚本化替身**：它证明"路线可重复、分段可续、预算不重开"，**不证明**"她自己过了一天"；
> 真实 Gemini 的有限运行由父级跑（600s / ≤6 次新增 HTTP，按实例既有更低上限）。
> **③ 未做**：24h soak、全库回归、全树 SHA、部署、提交；原实例/配置/Memory/密钥/MANIFEST 未触碰。
>
> 本轮不合格的说法：`连续生活已经验收`、`替身那一轮=她自主的一天`、`status 能读到的就是全部真实`。

- 仓库：`D:\nyairo-rc1-body-backend`（HEAD `41e818faaa24115fbf43db2977b15585c04144af`，既有 dirty 全保留，未 add/commit/reset）
- 施工单：`D:\NYAIRO-v0.4-连续日常生活-大阶段单.md`（C 段，独占写集）
- 决策记录：`.agents/notes/implemented/feature/2026-10-27-v04-lived-day-runtime-entry.md`

---

## 1. 写集（只动这些）

```
新增：components/phone/live_day.py                 连续段入口的实现（run/status/stop/summary）
新增：scripts/live_day.py                          操作者的一条命令（薄 CLI）
改：  components/life/scripts/autonomous_life_resident.py
       只加 *_segment_request_cap（lower-only）、_apply_segment_cap、summary 一个键
改：  components/life/scripts/autonomous_life_events.py
       只改 WorldOpportunityFeed 的 event ref 编码（结构性缺陷，见 §4）
新增：tests/test_live_day_runtime.py               16 例（11 平台无关 + 5 POSIX 连续流程）
新增：docs/engineering/v04-lived-day-runtime-result.md（本文件）
新增：.agents/notes/implemented/feature/2026-10-27-v04-lived-day-runtime-entry.md
```

未触碰：`autonomous_life_cycle.py` / `autonomous_life_assembly.py` /
`agency_cognition_adapter.py` / `agency_decision_ag1.py`（A 的写集），
`world_move_opportunity_source.py` / `world_action_bridge_adapter.py` / 任何 `life_daily_action_targets.py`
（B 的写集），`one_day_life.py`、`components/phone/service.py|server.py|demo.py|life.py`、
`components/world/**`、`components/supply/**`、任何实例 JSON / 配置 / 数据库 / 密钥 / `MANIFEST.json`。

## 2. 操作者的一条命令

```bash
# 一段有限的生活；owner 没在跑就由它自己按 demo.serve_instance 起，已经在跑就一个都不起
PYTHONDONTWRITEBYTECODE=1 python scripts/live_day.py run --instance /abs/phone-root \
    --duration-seconds 600 --max-new-requests 6 --text

python scripts/live_day.py status --instance /abs/phone-root [--live] [--text]
python scripts/live_day.py stop   --instance /abs/phone-root
```

| 参数 / 行为 | 语义 |
|---|---|
| `--duration-seconds` | 默认 **900s**（有限）；`1..86400`，超范围即 `DURATION_OUT_OF_RANGE`（exit 2）；`duration=None` 且无 `--max-ticks` 直接 `FINITE_BOUND_REQUIRED` |
| `--max-ticks N` | **本段**的 tick 数；入口按 `已记录 ticks + N` 换算成 resident 的绝对上界（`bounds.max_ticks_absolute` / `ticks_already_recorded`），否则第二段会在 1 tick 后结束（实测坑） |
| `--poll-seconds` | 交给 resident 自己的 poll（默认它自己的 180s） |
| `--max-new-requests N` | **只压低**这一段：`min(既有上限, 段开始时已用 + N)`；账本不可读则 `SEGMENT_REQUEST_CAP_UNVERIFIED` 拒绝，绝不开新窗口/重置账本/清 quiet |
| `--services auto\|always\|never` | auto（默认）：Supply socket 已在答就什么都不起；always：按 `demo.serve_instance` 起本段自己的 generation；never：要求操作者自己的 serve 在跑 |
| 结束/取消 | `life_run` 的 `finally` → resident 自己的 `stop()` 释放 writer lease；`finally` 再停**本命令启动**的 children；SIGINT/SIGTERM 走 `request_stop`，同样停 children（exit 130） |
| `status` | 纯读：不启动服务、不建库、不绑 socket、不碰 lock 文件；`--live` 才额外向**已在跑**的 World owner 要一次有界观测（15s 上限，超时报 `OWNER_READ_TIMEOUT`），读不到就 `UNKNOWN` + 原因 |
| `stop` | 只对这台实例自己 `resident_summary.json` 里记录的 pid 发 SIGTERM（POSIX）；Windows 上 `os.kill` 会硬杀、不跑 `finally`，因此如实拒绝并让操作者用 Ctrl-C |

不做的事（写死在实现里）：`prepare`/reset/重新 seed、生成新 run id、恢复旧业务备份、
`CHIYO_AUTONOMOUS_NEW_RUN_WINDOW`、强制 wake、clear quiet、把 Decisions 当重放权。

## 3. 循环真实语义（不新造总脑）

| 要求 | 实现 | 证据 |
|---|---|---|
| 旅行/停留未到点按 owner 自然推进 | resident 每 tick 先 `settle_world_journey()`（`autonomous_life_cycle.py:6108`），再走 runner | `tests/test_live_day_runtime.py:433-438`（ticks 的 `journey.journey_status` / 两个 tick） |
| 不每个 poll 重开 Decision | runner 自己的 `run_due()`：窗口没到 → `NOT_DUE`（silence），零请求；只有真实事件（世界可达集变化/资格开启/作品 head 变）/窗口到点才考虑 | 连续测试 test_2：第二段 `len(stub.requests)` 不变、`categories.silence>=1` |
| NOT_DUE 每 tick 维护一次 | `assembly.runner_tick` 先 `runner.maintain()` 一次，`run_due(maintain=False, maintained=...)` 复用同一次报告 | `autonomous_life_assembly.py:868-874`（A 侧既有实现，未改） |
| 安静 ≠ 故障；预算耗尽/被挡 ≠ 休息 | summary 的 `categories` 把 silence / idle_no_candidate / her_choice / acted / held_by_operator / blocked / unknown / fault 分开计数；`blocked_reasons` 只来自 wake 的 `detail` | `components/phone/live_day.py`（`_wake_counts`）、测试 test 1/2 |
| 停点续接 | 停点来自 owner：`baseline.work`（artifact_id/head_version_id/version_number/resumable）+ `work_selection.selected_by` + `foreground_activity_ref` + `stop_reason`；续跑就是再跑同一条命令 | test_1/test_2 的 `stop_point` 断言；`status` 同样适用 |
| 异常退出保状态 | resident 崩溃/异常：`life_run` 的 `finally` 仍 `stop()`（写 summary `running=false` + `stopped_at`），入口报 `RUN_FAILED`、lease `FREE`、`status` 可读且 `running_recorded=false` | test_3 |
| 取消也停 | `KeyboardInterrupt` 走出 `life_run` → 入口 `control.cancel()` + `report.status=CANCELLED` / exit 130；lease `FREE`、children `stopped` | test_4 |

## 4. 沿真实问题修的一处（跨模块但无人认领）

**现象**（第一版 smoke 立刻抓到）：`WORLD_OPPORTUNITY` 事件一发生，这一轮就是
`BLOCKED / DECISION_FAILED`：

```
AgencyDecisionError: trigger_ref='world:wrev:coastal_town_929711:r1|place:street_1'
must be a structured reference '<prefix>:<id>', not free text
```

原因：`autonomous_life_events.WorldOpportunityFeed` 把可达集签名第一条（`wrev:...|place:...`）
直接拼进 `ref`，而事件 `ref` 会成为 AG-1 的 `trigger_ref`，只接受
`agency_decision_ag1._STRUCTURED_REF_RE`（`^[a-zA-Z][a-zA-Z0-9_.-]*[:_][a-zA-Z0-9_.:/-]+$`）。
于是**每一次真实世界变化都被记成技术性 blocked**，且不花请求——"安静地坏着"。

修法（最小、只动编码）：`ref = "world:reachable:<sha256(signature)[:16]>"`。同一签名→同一 ref
（去重语义不变），不同可达集→不同 ref（仍是真实事件）。证据：修后同一实例再跑，事件路径不再出现
该错误（`tests/test_live_day_runtime.py:444` 断言 summary 中不含 `must be a structured reference`）。

## 5. 给操作者的有界 summary（`phone.live_day.summary.v1`）

只从既有记录重建，**不新 store、不写日记事件、不含私文正文**：

| 问题 | 键 | 来源 | 读不到时 |
|---|---|---|---|
| 现在在哪 | `where`（status/place_id/place_name/area_id/area_name/kind/here/source/observed_at/world_revision/unknowns） | 段的最后时刻（**在停 children 之前**）向 World owner 要一次观测；B 的 `daily_life_context` 若已 `unavailable`，用同一 provider 做**一次**只读 `observe()` 再问；否则退回 `world_journey_projection` | `UNKNOWN` + `unknowns[]`（如 `live_owner_unread:...`），不猜地点 |
| 手上什么 | `hands`（items/holding/free_slots/pose/orientation/hands_usable/source） | 同上观测的 `body`（B 的 `{slot:{object_id}}` → 用 `nearby[].label` 映射名字） | `UNKNOWN` + 原因 |
| 正在做什么 | `doing`（activity_ref/decision/outcome/status_word/since/pause/last_silence） | 本段**真的被考虑过**的最后一个 wake；`NOT_DUE`/`QUIET`/`DUPLICATE_EVENT` 只进 `last_silence` | `UNKNOWN`（`NO_CONSIDERED_WAKE_IN_THIS_SEGMENT`） |
| 停在哪 | `stop_point`（artifact_id/head_version_id/version_number/content_chars/resumable/selected_by/foreground_activity_ref/stopped_at/stop_reason） | resident 的 baseline（owner head） | `UNKNOWN`（`BASELINE_ABSENT`） |
| 下一次何时可考虑 | `next`（check_window_at/interval_minutes/last_check_at/journey_arrival_at/earlier_if） | `autonomous_events.json` 的 `last_check_at` + resident 的 `interval_minutes`；更早只可能是三类真实事件 | `UNKNOWN` + 原因 |
| 本段实际改变什么 | `changed`（wakes 计数/llm_calls/ticks/owner_versions/actions/model_requests/budget/segment_request_cap/coverage/last_wake） | wake 日志有界尾部（按段起点过滤）+ resident summary 的 counters/budget + coverage 账本 | 缺哪块就 `None`，不补 0 |

有界：wake/ticks/`nearby`/`opportunities` 条数由 `limit`（默认 12）截断；jsonl 只按
`TAIL_BYTES=262144` 读尾部；`limits.private_text_included=false`，`stop_point` 只带 `content_chars`
不带正文。`status` 的 summary 是**已记录历史**（`segment.scope=RECORDED_HISTORY`，
文本行叫"已记录改变"），`run` 的才是本段（`EXPLICIT_SEGMENT`）。

## 6. 验证（实际命令与结果）

Windows（`os.name == nt`，POSIX 连续类自动 skip；`PYTHONDONTWRITEBYTECODE=1`）：

```
cd tests
python -m unittest test_live_day_runtime -v
  → Ran 16 tests ... OK (skipped=5)        exit 0
```

WSL Ubuntu（Python 3.12，真 flock/AF_UNIX，repo 从 `/mnt/d` 读）：


```
cd /mnt/d/nyairo-rc1-body-backend/tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_live_day_runtime -v
  → Ran 16 tests in ~33s ... OK            exit 0

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_day_life_runtime_coverage test_day_life_quiet_runner
  → Ran 19 tests in 0.79s ... OK           exit 0     （相邻回归：resident 账本 + runner quiet）
```

连续流程（4 段，同一真实实例 `demo.prepare_demo(with_world=True)` 建的 root）：
`run_segment` → `demo.serve_instance`（Supply/World 真子进程）→ `life_run` → 真
`AutonomousLifeResident`/cycle/runner/AR-0 → 停 → 再跑 → 注入异常 → 注入取消。

CLI 实跑（WSL，实例的 `one-day/model_env` 指向本机 loopback 替身；替身明示非自主）：

```
python3 scripts/live_day.py run --instance /tmp/live-day-smoke-2770 \
        --max-ticks 1 --duration-seconds 120 --max-new-requests 2 --text
  → 现在在哪: home (home) [provider_daily_life_context]
    手上什么: 空（双手空闲：left_hand, right_hand） [provider_daily_life_context]
    正在做什么: unknown NO_ACTION [resident_wake_journal]
    停在哪: artifact:7b9d... v1 (BOUND_WORK) 停在 2026-10-19T13:40:03+00:00
    下一次可考虑: 窗口 2026-10-19T14:10:03+00:00（每 30 分钟；...）
    本段改变: acted=0 选择不动=1 安静=1 无合格事项=0 被挡=0 未知=0 请求=2
    本段: READY ticks=12 请求(本段)=1 服务启动=True lease=FREE     exit 0
python3 scripts/live_day.py status --instance ... --text   → 记录: running=False pid=... lease=FREE  exit 0
python3 scripts/live_day.py stop   --instance ...          → reason=ALREADY_STOPPED                exit 0
python3 scripts/live_day.py run ... --max-new-requests 0   → REQUEST_CAP_INVALID                  exit 2
python3 scripts/live_day.py run ... --duration-seconds 0   → DURATION_OUT_OF_RANGE                exit 2
```

夹具保留：测试目录、smoke 实例（`/tmp/live-day-smoke-2770`）、`/tmp/live-day-continuous-*` 全部留在
磁盘上，没有 `TemporaryDirectory` 清理，也没有物理删除。

## 7. 边界与已知限制

1. **替身 ≠ 自主一天**：连续测试的模型是脚本化 raw JSON；它验证入口/循环/预算/停点/异常，
   不验证她的判断。真实 Gemini 有限运行由父级跑（600s、≤6 新增 HTTP；入口的
   `--max-new-requests` 正是为此而设，且只会更低）。
2. **POSIX 专有**：连续类测试与 `stop` 需要 `flock`/AF_UNIX/SIGTERM；Windows 上连续类 skip、
   `stop` 如实拒绝（用 Ctrl-C）。`status`/`run` 的拒绝路径在 Windows 上可测（已测）。
3. **`status` 的"现在在哪"**：默认只读文件与 journey 投影；要看 owner 的实时观测需要
   `--live`，且只在这台实例的 World socket 由**别人**在跑时才有意义（本命令自己起 children 的
   那一轮，实时观测是在停 children 之前取的）。读不到就 `UNKNOWN`。
4. **`--max-ticks` 换算是读 summary/journal 得出的**：如果 summary 与 journal 都被清空，绝对上界
   会从 0 起算；这种状态本身已经说明实例记录被外部改过。
5. **`hands` 的名字**来自 B 的 `nearby[].label`；B 的 catalog 不可读时只报 owner 的 object_id。
6. **没有验证**：24h、跨机器、真实 provider 的预算耗尽、Supply 真实产物写入路径（本轮用替身
   模型给出的 `NO_ACTION`/`DEFER`，真实内容生成只在父级的 Gemini 段里发生）。

## 8. 与 A / B 的接口（已联通 / 仍需要）

- **已联通**：B 的 `daily_life_context`（`world_move_opportunity_source.py:1516`）被**只读**当作
  "现在在哪/手上什么/附近/机会/未知"的数据源；`status=available|unavailable`、`unknowns[{code,detail}]`
  两种拼法都读（旧的 `READY/UNAVAILABLE` 也兼容）。A 的 `settle_world_journey` /
  `resume_selection` / `maintain_waiting` / runner 的 `extra.waiting|step_readiness|reconsideration`
  都按既有形状读，未改。
- **仍需要 A**：`cycle.settle_world_journey()` 的返回形状保持 `journey_status/location_id/
  expected_arrival_at`；wake 的 `extra.owner_version_id|step_path` 一旦由 A/B 透传，
  `changed.owner_versions` 立刻从 0 变成真实值（当前恒为 0 是既有 R 决定，不是回归）。
- **仍需要 B**：`daily_life_context` 的 `observed_at`（目前 B 已给）、`nearby[].label`、
  `opportunities[].label/description`（报告只搬运，不评分）；`Prepare/Take/Place` 的 dailyact
  目标只要仍经 AR-0 与 Resolver，本入口不需要改动。
