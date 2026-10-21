# Agent Note: v0.4 连续生活入口 —— 一段真循环、一个下发上限、一份不撒谎的 summary

Status: implemented

## Problem

0.4 之前每一块都能单独跑，但没有"她自己过一段连续日常"的**一个**入口，也没有一个说法能把
"现在在哪 / 手上什么 / 正在做什么 / 停在哪 / 下一次何时"讲清楚：

1. **入口缺失且易被拼错。** `phone.life.life_run` 能跑 resident，但 owner（Supply/World）要么由操作者
   先自己 `serve`，要么就在旁边另开一个 CLI；两个入口之间的顺序、代际（socket generation）、失败后的
   收尾全靠人手。用错一次就会出现"新的 serve 代际 + 旧的 binding"或"resident 已停、children 还在"。
2. **"一段"在 resident 里不是"一段"。** `resident.run(max_ticks=N)` 比的是**累计** tick（启动时从
   journal 回放），所以第二次 `--max-ticks 3` 实际只跑 1 tick 就退出。实测坑，不是理论问题。
3. **短段没法封死花费。** 既有请求预算只能整天/整个 run 窗口设上限；临时跑 600 秒又想最多 6 次
   HTTP，既不能新开窗口（那就等于发新额度），也不能改账本。
4. **"安静"没被好好读完。** `NOT_DUE`/`QUIET` 是关着的窗口与已考虑过的状态；它们和
   `BLOCKED`/`ERROR`（技术性故障）、`NO_ACTION`/`DEFER`（她自己的选择）必须在报告里分开。此前没有
   一个给操作者的视图区分这三者，也没有"下一次何时可考虑"。
5. **一个真实的编码缺陷在第一次连续跑就暴露了。** `WORLD_OPPORTUNITY` 事件的 `ref` 直接把
   `wrev:...|place:street_1` 拼进去，而它会被当作 AG-1 的 `trigger_ref`（只接受
   `<prefix>:<id>`），于是**每一次真实世界变化都被记成 `BLOCKED / DECISION_FAILED`**，还不花请求——
   看起来像"她没动"，其实是事件根本进不去。

## Decision

新增 `components/phone/live_day.py`（实现）+ `scripts/live_day.py`（唯一入口命令），并给 resident 加
一个**只能压低**的段上限；其余一律复用既有 owner：

1. **一个入口、两个复用。** `live_day run --instance ROOT [--duration-seconds/--max-ticks/
   --poll-seconds/--max-new-requests]` 复用 `demo.serve_instance`（owner 已在答就一个都不起；否则起
   本段自己的 generation，`finally` 只停自己起的 children）与 `phone.life.life_run`（同一个
   `AutonomousLifeResident`、同一把 writer lease、同一份 run 窗口）。默认 900 秒；`duration=None`
   又没有 `--max-ticks` 直接 `FINITE_BOUND_REQUIRED`；> 86400 秒拒绝。
2. **段上限只压低。** resident 新增 `segment_request_cap`：启动时读账本自己的 `view()`，令
   `utc_day_max = min(既有, 已用 + cap)`、`run_window_max = min(既有, 已用 + cap)`，账本不可读则
   `SEGMENT_REQUEST_CAP_UNVERIFIED` 拒绝（"最多 N 次"不能只是口号）。不开窗口、不重置、不提上限；
   summary 里 `raised_any_ceiling` 恒为 false。
3. **`--max-ticks` 换算成绝对上界。** 入口读 summary/journal 的已记录 ticks，`绝对 = 已记录 + N`，
   并把两者都写进 `bounds`，这样第二段、第三段仍是"这一段 N tick"。
4. **取消也停。** 入口在主线程装 SIGINT/SIGTERM → `resident.request_stop(reason=...)`；`life_run` 的
   `finally` 释放 lease；入口自己的 `finally` 停 children。`KeyboardInterrupt` 走出 `life_run` 时报
   `CANCELLED` / exit 130，而不是伪装成崩溃。
5. **报告只从既有记录重建。** `segment_summary` 读 resident summary（counters/baseline/budget/
   interval/model_calls_detail/segment_request_cap）+ wake 日志的有界尾部（按段起点过滤）+ 事件状态
   + pause 位 + journey 投影 + coverage 账本；`where/hands` 来自**停 children 之前**向 World owner
   要的一次有界观测（B 的 `daily_life_context` 若 `unavailable/NO_OBSERVATION`，用同一 provider 做一次
   只读 `observe()` 再问），读不到就 `UNKNOWN` + `unknowns[]`。不新 store、不写日记、不带正文
   （`stop_point` 只有 `content_chars`），`limits.private_text_included=false`。
6. **`status` 纯读、`stop` 只杀自己记录里的那个 pid。** `status` 不启动服务、不建库、不绑 socket、
   不碰 lock 文件；`--live` 才额外读 owner（15 秒上限，超时报 `OWNER_READ_TIMEOUT`）。`stop` 只在
   POSIX 上对 summary 记录的 pid 发 SIGTERM；Windows 上 `os.kill` 会硬杀且不跑 `finally`，因此如实
   拒绝并让操作者用 Ctrl-C。
7. **修掉那个事件编码缺陷**（`autonomous_life_events.py`）：`ref = "world:reachable:<sha256[:16]>"`。
   同一签名→同一 ref（去重不变），不同可达集→不同 ref（仍是真实事件），且是结构化引用。

## Alternatives considered

- **另建一个"生活总脑"/LifeManager 存 summary 与停点** —— 最强理由是"一次读一个文件、最省事、也能
  存下操作者想要的字段"。否决原因：那就是第二个可写真相源，会和 resident summary / wake 日志 /
  owner 的 head 竞争，且"现在在哪"会变成上一次写进 store 的旧值。现在只读既有记录，读不到就
  `UNKNOWN`。
- **入口自己起 `phone.py serve` 子进程** —— 最强理由是"和操作者手动 `serve` 完全同一条路、不用在
  同一进程里跑 HTTP"。否决原因：多一个进程就多一套 pid/日志/收尾，而且 resident 要读的 live binding
  还是 serve 写出来的；`demo.serve_instance` 本来就有 `stop_event`/`on_ready`/`announce=False`，在
  本进程按同样顺序调用即可，`finally` 由它自己停 children（取消也停），更少分叉。
- **用 `CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_*` 环境变量传段上限** —— 最强理由是"不碰 resident、只用
  配置"。否决原因：`phone.life.build_binding` 会先剥 `CHIYO_AUTONOMOUS_*` 再把 nested 实例记录的值
  覆盖回来，传进去的更低值会被静默丢掉；而且 env 无法表达"已用 + N"。现在由 resident 在构造预算
  之后用账本自己的 `view()` 算 `min(...)`。
- **让 `--max-ticks` 就是 resident 的原始语义（累计）** —— 最强理由是"不改口径、少一层换算"。否决
  原因：实测第二段只跑 1 tick，"这一段跑 N tick"是操作者的直觉，且不换算就等于悄悄改变段的含义。
  现在换算并把 `ticks_already_recorded` / `max_ticks_absolute` 都报出来。
- **把 `status` 做成"读不到就报运行中/上次已知"** —— 最强理由是"操作者更想看到非空的答案"。否决
  原因：那不是事实。现在 `UNKNOWN` + `unknowns[]` + 明确的 `live_owner_read.status`，并检查 pid
  存活以区分"记录说在跑但进程没了"。
- **把世界事件那条 `DECISION_FAILED` 当成"她今天不想动"** —— 最强理由是"反正没花钱、也算安静"。否决
  原因：它是 `blocked`（技术性拒绝），把它计入沉默或她的选择会永久掩盖真实世界变化进不来的事实。
  现在按结构化引用修好编码，测试断言报告里不再出现那条错误。

## Consequences

- **收益**：操作者一条命令就能起一段有限生活（owner 缺就起、在跑就不起），结束/取消都释放 lease 并
  停自己起的 children；短段能用 `--max-new-requests` 封死新增 HTTP（只降不升、不新窗口、不重置账本）；
  `--max-ticks` 真的按段计；报告能回答那五个问题，并把 silence / 她的选择 / 被挡 / 未知分开；
  `status` 真的只读。
- **代价与已知上限**：① 连续测试的模型是脚本化替身，验证路线与收尾，**不验证**她的判断；② 连续类
  测试与 `stop` 仅 POSIX（Windows skip / 拒绝，用 Ctrl-C）；③ `status` 的"现在在哪"默认来自文件与
  journey 投影，实时观测要 `--live` 且只对**已在跑**的 owner 有意义；④ `changed.owner_versions`
  仍为 0（等 A/B 在 wake `extra` 透传版本，属既有 R 决定）；⑤ `hands` 的名字依赖 B 的
  `nearby[].label`，catalog 不可读时只报 object_id；⑥ 段上限在"summary 与 journal 都被清空"时
  只能从 0 起算（这种状态本身已说明记录被外部改过）。
- 相邻既有决定：[一天生活运行时间证据](./2026-10-26-v04-day-life-runtime-coverage.md)、
  [一天生活核心连续性](../architecture/2026-10-26-v04-day-life-core-continuity.md)、
  [单一写者隔离](../process/2026-10-16-single-writer-isolation.md)、
  [世界身体常驻与休息](./2026-10-21-v04-world-body-resident-and-rest.md)。
- 机制与结果：[C 侧交付结果](../../../../docs/engineering/v04-lived-day-runtime-result.md)。

## Verification

- Windows（POSIX 连续类自动 skip）：`tests/` 下 `python -m unittest test_live_day_runtime -v`
  → `Ran 16 tests ... OK (skipped=5)`，exit 0。
- WSL Ubuntu（真 flock/AF_UNIX）：`cd /mnt/d/nyairo-rc1-body-backend/tests`，
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_live_day_runtime -v` → `Ran 16 tests ... OK`
  （约 33 秒），exit 0；相邻回归 `test_day_life_runtime_coverage test_day_life_quiet_runner` →
  `Ran 19 tests ... OK`。
- 连续流程（同一真实实例，`demo.prepare_demo(with_world=True)`）：起 owner（真 Supply/World 子进程）
  → 2 tick（第一窗 `NO_ACTION`、第二 tick `NOT_DUE`、请求 ≤1）→ 停（lease `FREE`、children stopped）
  → 第二段（同 run 窗口、`NOT_DUE` 零请求、`max_ticks_absolute = 已记录+1`、停点不变）→ 注入异常
  （`RUN_FAILED`、summary `running=false`、lease `FREE`、无额外请求）→ 注入取消（`CANCELLED`、
  exit 130、lease `FREE`、children stopped）。
- 段上限算术：已用 10 / 上限 96 / cap 6 → 16；既有上限 3 → 仍 3（不提高）；账本 `corrupt` →
  `SEGMENT_REQUEST_CAP_UNVERIFIED` 且上限不变；无 cap → 无操作。
- CLI 实跑（WSL）：`run --text` → `现在在哪: home [provider_daily_life_context]`、
  `本段: READY ... lease=FREE`，exit 0；`status --text` → `running=False ... lease=FREE`，exit 0；
  `stop` → `ALREADY_STOPPED`，exit 0；`--max-new-requests 0` → `REQUEST_CAP_INVALID` exit 2；
  `--duration-seconds 0` → `DURATION_OUT_OF_RANGE` exit 2。
- 未做：真实 Gemini 运行（父级负责，600 秒 / ≤6 新增 HTTP）、24h soak、全库回归、全树 SHA、
  `py_compile`、物理删除与 `TemporaryDirectory` 清理（夹具目录全部保留）。原实例/配置/Memory/密钥/
  `MANIFEST.json` 与两个历史 fix 脚本未触碰，未提交、未部署。
