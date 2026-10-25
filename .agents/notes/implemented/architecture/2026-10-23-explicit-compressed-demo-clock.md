# Agent Note: 显式压缩演示模式（COMPRESSED_DEMO）的独立业务时钟

Status: implemented

## Problem

日常链要按“一个逻辑日”验收，但真实 24 小时墙钟不可等待。此前只有两种坏做法：
改系统时间/`TZ`、或猴补全局 `datetime`——两者都会把真实安全面（provider 超时、
lease、权限过期、请求账本的真实 UTC 日、审计 `recorded_at`）一起加速，等于用
假时间换绿灯。Life 已有 runner/resident 的 clock 注入点，但只用于测试的固定时刻，
没有一份可持久、可恢复、可判域的共享业务时钟，也没有“哪些 must 不加速”的边界。

## Decision

新增 `components/life/scripts/demo_business_clock.py` 与入口 `scripts/fast_day.py`：

- **唯一业务权威**：`BusinessClockAuthority` 由 `fast_day start` 进程持有，写
  `fastday/fastday_clock.json`（domain_id/run_id/rate/anchor/checkpoint/horizon/state）。
  单进程内 `logical = anchor + monotonic_elapsed * rate`，到 24 逻辑小时 horizon 钳位。
- **客户端只读**：`BusinessClockClient` 只能读同域映射；缺文件、损坏、`rate` 非法、
  domain/run 不匹配一律具名 fail closed，绝不退回系统墙钟。
- **只有业务面接时钟**：resident 的真实 `_clock`/`_monotonic`（进程覆盖账、lease、
  真实 deadline）不动；业务 now 经 `assemble_autonomous_life(clock=...)` 传给
  runner 的自然窗口/安静、cycle 的 `_now_iso()`（未来事项到期、LR-5 业务证据、
  步骤 receipt、世界机会过期）、`settle_world_journey(observed_at=...)` 的旅程进度，
  以及 `WorldBodyService(clock=...)` 的 Body 暴露/恢复。World 进程用
  `phone.py owner-world --business-clock/--clock-domain/--clock-run-id` 读同一域。
- **业务/安全分域的接缝收口**：正式 run 实际使用的计时 adapter
  （`RealJourneyWorldActionAdapter` → `JourneyWorldResolver`）把同一个业务 now 传给
  `world_journey.accept_journey(now=...)`，出发/到达与 cycle 的
  `settle_world_journey(observed_at=...)` 同域；`WorldResolver` 提交 MOVE 时把
  自己的 `_clock()` 传给 `apply_move_to(now=...)`，让 owner 的 travel
  `started_at/expected_arrival_at` 也落在业务域。安全面保持真实：一次性授权
  `AuthorizationRegistry.issue/verify_and_consume` 仍用 `time.time()`；World 权威
  lease 的内核 flock 与时间无关，其 marker `acquired_at` 固定写真实墙钟；
  Body consequence/receipt 的 `applied_at/consumed_at` 是业务证据，随业务域。
  没有注入时钟时这些 `now` 都是原来的系统 UTC，普通路径行为不变。
- **冻结与重启**：外部 pause/stop 走独立 `fastday/fastday_control.json`，authority
  低频轮询；冻结期间业务时间不动。重启从持久检查点继续，`downtime_frozen` 如实
  记录，不按墙钟补跑、不重放已完成 tick。`start` 只允许 `<=7200` 真实秒，并按
  `horizon_remaining/rate` 截断。
- **真实墙钟累计**：单独一份 `fastday/fastday_wall.json` 记本 run 的
  `consumed_seconds`（只本功能元数据）。`start` 开段、正常收尾关段，暂停期间墙钟照
  算；进程被 kill 留下未收尾段时，下一次启动按墙钟时间戳**保守**把整段扣掉（停机也
  算），不凭逻辑余量退款。每次 `duration` 都同时被「逻辑余量」与「7200 减累计墙钟」
  截断；累计到上限就具名 `WALL_BUDGET_EXHAUSTED` 拒绝，不再借回暂停/重启耗掉的墙钟。
  账本损坏或 run 身份不符 fail closed，首次缺失才按 0 初始化。
- **并发与额度**：一个 domain 同时只允许一个 authority（同机看 pid 存活，跨机看
  心跳）；实验额外上限 `ExperimentRequestCap` 持久，跨重启与真实 UTC 跨日都不
  刷新，账本不可读 fail closed。默认路径仍是总限 96、实验新增 <=82（`hard_config_max`
  兼容字段现在表示本实例授权总限，默认 96）。上限检查落在**每一次请求
  预留**的边界（`CappedRequestBudget` 包住实例自己的 `LifeRequestBudget`），一次
  wake 里发多个请求也逐个计费，失败/取消不退款。实验上限的读取、账本预留和
  `reserved_calls` 高水位写在**同一把跨进程锁**里。
- **显式实例额度绑定（2026-10-23，用户授权总限 300）**：`fast_day prepare` 新增
  `--request-total-limit/--historical-requests/--max-new-requests/--max-cognition-calls-per-day`。
  显式值只写进**这一个新实例**的 `CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY`/
  `_PER_RUN_WINDOW`/认知每日配置与 `experiment_budget.json`（本次为 300/14/286，
  认知 300），不把 300 变成全项目默认，也不改旧实例。规则是
  `历史 + 新增 <= 总限`、`总限 <= 300`、默认 96 仍另受 82 约束；越界/负数/非整数在
  `prepare` 写任何文件前具名拒绝。上限文件记录 `authorized_total_limit`/
  `historical_requests`，历史基线只用于证明总限，不写成本 run 的真实调用
  （`baseline_run_window_calls` 仍为 0）。旧元数据缺这两个字段时按 96/0 解释，
  不静默升级；文件被单独抬高时 `load()` 以「与实例配置不一致」拒绝。
  `ExperimentRequestCap` 自己记 `reserved_calls`：账本看到的预留数比它少（含账本被
  回滚、被删）或整块替换成另一个 run 时，**下一次预留前**就具名
  `EXPERIMENT_LEDGER_ROLLBACK`/`RUN_MISMATCH` 拒绝，不读成 0、不自动补写账本、不
  退款、也不继续预留；只有全新实例第一次预留（`reserved_calls == 0`）按 0 初始化。
- **可运行装配**：`fast_day prepare` 默认在全新根里走既有入口建**现成实例**：
  `demo.prepare_demo(with_world=True, run_id=..., seed_work_artifact=False)` 建真
  Supply/World 与 workspace，阅读材料用 operator 提供的公版《桃花源记》65 字节选，
  `autonomous_life_control.set_enabled` 打开本实例开关，
  `phone_life.enable_instance_memory` 新建实例自己的空 Memory。`--run-id` 经既有
  `one_day_life.prepare(run_id=...)` 落成 `run:<name>` 的 `CHIYO_AUTONOMOUS_RUN_ID`，
  clock/请求账本/实验上限/墙钟账本/resident 环境/跨进程恢复全用同一个身份；父进程
  环境携带另一个 run 时在写任何文件前 `INHERITED_RUN_CONFLICT` 拒绝。专用开关
  `seed_work_artifact=False` 不建 demo 占位作品：没有合成种子被当成她的历史，首次
  作品由她自己后来真实创建；普通 `prepare_demo` 默认行为不变。
  `--clock-only` 只准备时钟/额度，不建实例（时钟测试用）。`fast_day start` 在
  supervisor 进程里起本根自己的 Supply/World 子进程（复用 `demo.owner_children`，
  **不绑 HTTP**），World 子进程用 `--business-clock/--clock-domain/--clock-run-id`
  读同一域；resident 是独立子进程，环境来自实例自己的绑定，run 不一致直接拒绝，
  不再 `setdefault` 透传。停止只收本记录里的 supervisor/resident/owner pid。

## Alternatives considered

1. **改系统时间/`TZ` 或缩短 sleep**：最强理由是改动面最小、所有模块“自动”快进。
   否决：它同时加速 provider 超时、lease、权限过期与真实 UTC 日，安全边界会被
   悄悄削弱，且不可跨进程判域。
2. **猴补全局 `datetime.now`**：最强理由是无需改调用点。否决：同一进程里业务与
   安全时间会被无差别替换，无法证明“真实过期仍拒”，也会污染审计。
3. **给每个函数加 `now` 参数、批量替换**：最强理由是最显式。否决：8500 行 cycle
   的大量调用点会把改动面和回归风险放大，且多数路径已经通过 `observed_at` 传入；
   只对真正缺口的入口补 `clock`/`observed_at` 更小且可证。
4. **把业务时钟放在 env 里的固定 rate 全局配置**：否决，无法表达暂停/检查点/域。

## Consequences

- 默认生产不变：没有 `NYA02_FASTDAY_CLOCK` 时 resident 不加载任何业务时钟，
  `time_domain` 报 `REAL_TIME`，行为与以前一致。
- 普通路径下 cycle 的 `_now_iso()` 仍是系统 UTC 墙钟；只有注入业务时钟时才变。
- 逻辑日成功与真实 24h soak 仍然互不相认：压缩模式写 `not_real_24h: true`，
  24h/生产日覆盖永远不能据它 PASS。
- 仍保留的边界：`CycleBudget` 的“每日轮数”按真实本地日计（压缩日会更保守）；
  WWW/HTTP 面在本模式默认不启动（只起本地 owner 子进程）。
- **包装器的读面契约（2026-10-23 修复）**：`CappedRequestBudget.view()` 返回
  「实例账本的完整视图 + 顶层实验读口（`used`/`remaining`/`allowed`/`ledger` 等）
  + 具名子视图 `experiment_cap`」。此前它只回实验视图，`utc_day_calls`/`state`
  等账本字段全部缺失，`RoundBudget.cognition_used_today()` 按既有 fail-closed
  语义把空账本读成「96 已用尽」；真实 fastday-a 因此每个 wake 在预留前 BLOCKED，
  13 次无 action、0 模型调用。修复只补读面合并，不改 82/96 上限、每请求跨进程锁、
  回滚/换 run/坏账拒绝，也不改「预留发生在发送之前」。真实字段与实验字段互不冒充：
  账本字段留在顶层，实验视图完整放进 `experiment_cap`。
  `tests/test_fastday_clock.py::WrappedBudgetReadSurfaceTest` 用真对象复现该装配
  （含 `BudgetedCognitionAdapter` 离线发送替身）；`fast_day_smoke.py` 增加独立目录
  的零网络预算边界探针：空账本认知可用、一次发送同时推进真实与实验计数、
  用满与坏账仍 fail closed、逻辑日快进不刷新真实 UTC 日或实验额度。另加显式
  300/14/286 探针：真实 CLI `prepare --clock-only` 的 fastday.json 与上限文件绑定，
  同一配置下空账本认知 0/300 可用，286 次离线发送后第 287 次在发送前被拒。
- 实验上限的“已用”取「同一 run 窗口计数减基线」「本闸门自己的 `reserved_calls`」
  「历史高水位」三者最大值（数字上只增不减）。账本损坏/整块换 run 是
  `UNREADABLE`/`RUN_MISMATCH`；账本预留数少于 `reserved_calls`（回滚或删档）是
  `ROLLED_BACK`，**下一次预留前**拒绝。三者都 fail closed，**不会**把缺口读成
  0 次免费请求，也不会自动补写账本；早先“回滚后仍可再预留一次”的实现与说法已被
  推翻并有负控。
- 零模型冒烟走 `--no-act --offline-scripted`：装配的是既有
  `DeterministicContinuationWriter` 占位，只把 runtime 建起来，summary 明确标
  `offline_scripted: true`；它不产生她的文字，也不代表真实模型链或逻辑一天成功。
- **World 机会窗口与终点标签（2026-10-23 收口）**：业务时钟此前只进了 cycle 的
  `_now_iso()` 与 journey，`WorldMoveOpportunityProvider` 仍按真实墙钟给机会盖
  `valid_from/valid_until`。压缩日里两套时间迅速分叉（b 实例 12x），AG-0 的窗口校验
  于是把全部真实观察判成 `NOT_YET_OPEN`/`EXPIRED`：她能看见的 `place:`/`stay:`/`bodyact:`
  一个都进不了候选，四个空窗口被记成 `RULE_RESOLVED/NO_LEGAL_CANDIDATE`。
  修复只接既有接缝：cycle 把本回合的 `observed_at` 交给
  `provider.observe(occurred_at=...)`；`resolve_world_provider(clock=...)` 在压缩模式下
  把业务时钟同时作为 provider 的 `now`/`observed_now`（catalog 营业事实同域）。
  活着的窗口经真实 AG-0 源照常成为候选；过期/撤销/无源仍为 0，资格门未削弱。
  同一收口让**正常到达逻辑 horizon** 的 run 调既有 `authority.stop()` 落 `STOPPED`
  （不再留 `RUNNING` 标签）；未到 horizon 的短窗口仍 `checkpoint`/`RUNNING`，可同 run
  重启。证据：`tests/test_observation_candidate_supply.py`（真实
  `WorldOpportunityCandidateSource` 正负控）、`tests/test_fastday_runtime.py`
  （horizon→STOPPED 且重开被拒、短窗口保持 RUNNING）、
  `scripts/observation_candidate_smoke.py`（真实 Supply owner→候选 + 四项 CLI 可见结果）。
- 测试：`tests/test_fastday_clock.py` 与 `tests/test_fastday_runtime.py` 覆盖
  rate/钳位/冻结恢复/重启/并发预留/账本回滚与删档拒绝/实验上限/显式 300/14/286
  绑定与 286→287 拒绝/默认 96/82 兼容/墙钟累计与崩溃
  恢复/路径逃逸/真实 run 身份绑定与继承冲突/真实 owner grant 过期与逻辑跨日/
  真实子进程域相符与拒绝/真实 World owner 在业务 +24h 下仍按真实时间签发与消费
  授权（真实已过期授权被 gateway 以 `AUTHORIZATION_EXPIRED` 拒绝）、真实计时
  adapter 的出发时刻落在业务域、权威 marker 写真实墙钟/零模型 CLI 冒烟；
  `scripts/fast_day_smoke.py` 是一条自建隔离根、真实 Supply/World/resident 进程、
  0 产品请求的完整 CLI 冒烟（prepare→status→start→pause/resume→stop→同 run 重启）。
