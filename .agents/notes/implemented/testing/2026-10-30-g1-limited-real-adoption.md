# Agent Note: G1 既有同作品闭环保留机制，把决定权交给真实模型

Status: implemented

## Problem

`2026-10-28-g1-same-work-recovery` 已经把「中断 → 跨 PID 重启 → UNKNOWN 对账 → CAS 冲突 →
NO_ACTION → 继续」串成同一件作品上的一条链，并 28 项 CHECK 全过。但它的 Decision 与内容都是
脚本替身：那条链证明的是**机制**接得上，不能回答"真实模型自己会不会这样走"。
如果把真实 client 塞进那份 demo、却仍然由脚本挑候选，得到的是一个演示，不是采用证据。

缺的是一个入口：同一件作品、同一套正式 owner/runtime/AG-0/AG-1/AG-2/AR-0，但候选由既有
AG-0/owner 供给、由真实模型从中选择，内容也由真实模型写；同时预算、真实时间窗口、operator
中断和两处故障注入都必须有界、可核对，而且模型没有那样选时不能替它改。

## Decision

新增 `scripts/g1_real_adoption.py`（`offline-check` / `prepare` / `audit` / `run`），按
[计划](../../../../docs/engineering/g1-limited-real-adoption-plan-20261030.md)与
[runbook](../../../../docs/engineering/g1-limited-real-adoption-runbook-20261030.md)实现：

- 唯一 root/run/域；本批新增请求硬上限 20，写在实例自己的持久账本里，P1/P2 共用；
  运行窗口 1800 真实秒用 `monotonic` + 墙钟写进根里，父进程在首个请求前落盘，重开不延长，
  另受 17:00 CST 约束。
- `prepare` 零模型：真实 Supply、空 Memory、一份中性 operator 材料和一件明确标注的对照作品；
  `run` 需要 `--execute-approved`、未 started、账本 0、参数与固定 provider 完全一致。
- 真实 run 的唯一决策触发是生产 `runner_tick` 的 due/事件门；实例的检查窗口是 `prepare` 前
  固定的 1 分钟（不是每轮改 trigger，业务时钟不快进）。P1/P2 各是不同的子进程。
- Decision/内容都走正式 adapter，两个发送口共用一把「截止/供应商失败即停」的锁；每次发送前的
  预留落在同一本持久账本上，schema 重试一律拒绝。
- 两处故障注入只在既有 `adapter.submit` 接缝：丢回执用 `fault_injection_drop_response_after_write`，
  并发 head 用同一 owner 的另一个 writer；各只一次、只对作品 A 的 commit 写入生效。
- UNKNOWN 只调 `reconcile_action`（内部走 owner 的 `resolve_operation`），零裸重发；CAS 拒绝
  不造假回执/结果；NO_ACTION 只在模型真的选了且无附带写时计。
- 判定必须带对应证据，不按状态顺序倒推：P1 的 A 要本轮 `owner_version_created=true`、本轮
  receipt 指向该作品与正式 Activity，且 owner 重读回来的对象正是这件作品、head 与本轮版本 ref
  指向同一版本；P2 只有作品与 P1 Activity 精确匹配才推进。UNKNOWN 必须本次注入确实触发、
  原 operation 查到 `CONFIRMED_SUCCEEDED` 且有可核验证据；CAS 必须注入的并发写真的落 owner、
  head 真的推进，且原 Action 权威原因是 `CONFLICT`；任意 FAILED/GATE_OFF/先验拒都不算。
- 版本 ref 的等价规则只认现有链证明的三种形状，指向同一 owner 版本：
  `artifact-version:<uuid4 hex>`（owner 铸 id 原样，
  `components/supply/lifysupply/artifact/store.py:707`）、
  `artifactversion:artifact-version:<hex>`（Life 在 owner 原样外再包一层，
  `autonomous_life_cycle._owner_evidence` 第 9638 行）、
  `artifactversion:<hex>`（Life 先剥 owner 前缀再包一层，
  `autonomous_life_cycle.py` 第 4232—4234、9315 行）。比较时只把这三种还原成版本号；
  重复包装、裸 hex、`checkpoint:`/`receipt:` 等其它命名空间一律返回空、不参与匹配。
  身份侧同时用 owner 自己回的 `ArtifactResumePoint.artifact_id` 与 receipt 作品配对：
  换一件作品、或把另一件的 head 凑成同值，都不会确认。
- 发送口抛异常（含 `content.complete`）先关整批锁再抛出；停因同时写入根里的
  `g1_batch_stop.json`，P2 的新锁读到同样拒发，失败不会因为换进程复活。
- 结果分列六个场景的 `coverage`，缺一项就记 `NOT_COVERED`；audit 只输出 id/枚举/计数/布尔。
- 第二批（2026-10-30 批准）只做身份适配，不改行为：CLI 增加封闭的 `--batch a|b`，缺省 a
  与第一条完全一致。a 保留原 root/run/域/历史 98；b 固定
  `/root/nyairo-g1-real-20261030-b`、独立 authority `…-b-memory-authority`、
  `run:g1-real-20261030-b`、`g1:real:20261030-b`、历史 101。身份经 `_identity()` 解析，
  不再直接读模块常量；phase 子进程从 config 读回批次并复核 prereg/batch 的 run/域/root，
  缺身份、错批、错 root、另一批的 prereg 一律 `BATCH_IDENTITY_MISMATCH`。
- 第三批 c（2026-10-30 批准，[c 计划](../../../../docs/engineering/g1-limited-real-adoption-c-plan-20261030.md)）：
  新增封闭身份 `c`，root `/root/nyairo-g1-real-20261030-c`、独立 authority
  `…-c-memory-authority`、`run:g1-real-20261030-c`、`g1:real:20261030-c`、历史 121。
  `BatchProfile` 新增 `deadline` 字段，运行期的窗口 `deadline_wall`/`deadline_basis`、
  `prereg.deadline`、开跑前截止门、phase config 与 `audit.deadline` 统一从**所选批次**取：
  a/b 仍是 `2026-10-30T17:00:00+08:00`，c 是 `2026-10-31T01:00:00+08:00`；模块级
  `DEADLINE` 保留为 a 的兼容常量。prereg/config 的截止与所选批次不符或缺失一律
  `BATCH_IDENTITY_MISMATCH`。
- `prepare` 在写入前核新 root 与 authority 都新鲜；已有 authority 直接
  `MEMORY_AUTHORITY_NOT_FRESH` 拒绝，不默默复用、不删除重建。
- 第四批 **d 不是 G1 批**（2026-10-30 监护已批准身份与两个布尔）：
  [预登记](../../../../docs/engineering/pause-material-observation-prereg-20261030.md)。
  身份 root `/root/nyairo-pause-observe-20261030-d`、独立 authority `…-d-memory-authority`、
  `run:pause-observe-20261030-d`、`pause-observe:20261030-d`、历史 141、同一个
  `2026-10-31T01:00:00+08:00` 截止。
  **身份注册（收尾后）**：`BATCH_PROFILES` 只保留 a/b/c 三批原值不动；d 以独立
  `D_PROFILE` 注册，运行期可选表是 `MODE_PROFILES = {**BATCH_PROFILES, "d": D_PROFILE}`，
  `_select_batch`、`_check_root` 的"另一批"判定和 CLI 的 `BATCH_CHOICES` 都走它。
  d 不塞回旧集合，所以旧测试里硬编码的 `set(BATCH_PROFILES) == {"a","b","c"}` 不需要改。
  做法是**只追加一条独立路径**，不复用 G1 的 phase：`prepare` 只建 `demo.prepare_demo`
  的空实例（不启 Supply、不调用 `_provision_operator_material`，所以没有材料/对照/
  `mark_unclear`/later 事项）；唯一 phase `natural` 先在同一实例根上起**标准 World owner**
  （`demo.owner_children(...)["world"]`，gate 显式 `off`、父 shell 的 `canonical` 不继承；
  旧 `gateway.sock` 只由 `demo.retire_socket` 改名保留，不 unlink；socket `wait_ready` 之后
  才装配 runtime，且 runtime env 的 `WORLD_BODY_SOCKET` 必须等于这个 live child 的 socket），
  再走真 owner + 正式 runtime + 正式 `evaluate_once`/`runner_tick`，从第一轮自然走到
  cap/窗口/发送异常；`finally` 用标准 `ChildService.stop()` 依次停 runtime → world → supply，
  不删 socket，只核 `socket_live_after`。**不构造 `SubmissionInjector`、不暂停重启、
  不注入 UNKNOWN/CAS**，`coverage` 恒空、结果只在 `OBSERVED`/`NOT_COVERED`。
  d 的 trace 多两个布尔 `fact_sentence_present`/`tail_present`：只读**同一次**
  `build_prompt` 返回的 `situation_context.pause_condition.how`，用两条编译期常量逐字
  包含判断，与 `send_status` 同一行；取不到就是两个 `null`。
  a/b/c 的 profile/phase/记录字段与 trace 一个都没多。
- 装配层的一个既有顺序问题在 scripts 层最小处理（不改 `components/`）：
  `assemble_autonomous_life` 先构造 cycle、之后才 `resolve_world_provider`，因此 cycle
  拿到的是构造时的 `None`，真实 READY 的 provider 只在返回的 `assembled` 里。d 的
  `_build_runtime(..., world_feed=True)` 把这个**已经真实 `get_status()` 读回 READY 的
  同一个对象**接回 cycle；a/b/c 不传该开关，行为不变。phase 报告新增安全元数据
  `world`（started/pid/socket/gate/ready/status/reason）与 `close_world`
  （pid/exit_code/socket 与 `socket_live_after`），`observation` 新增
  `world_candidates_per_request`（同次请求里 `source_kind=WORLD_OPPORTUNITY` 的候选数）。
- 首步装配的第二个缺口（2026-10-30 收尾，修单只动 `scripts/` 与 d 测试）：原
  `_build_runtime` 向 `build_production_life_runtime` 传 `world_action_adapter=None`，
  `world_feed` 只把感知 provider 接回 cycle。cycle 的 `_world_step_enabled` 要求
  provider 与 `runtime.world_action_adapter` **都**非 None，缺一半时 START 只建 Activity，
  首步/Action 根本不构造（原 d 报告的三件空 `executable_step_refs`/`action_refs`）。
  修法是沿用 resident 的标准解析口：`world_feed=True` 时在构造 runtime **之前**用
  `assembly.resolve_world_provider(env)` 取一次真实 World 状态；READY 时用
  `autonomous_life_resident.resolve_world_action_adapter(env, world)` 构造动作 adapter
  （与 resident 同一实现，不另造 resolver/owner），把 adapter 交给 runtime、把**同一个
  provider** 交给 `assemble_autonomous_life`，因而只解析一次。`world_feed=False`
  （a/b/c）仍是原来的 `None` 与原行为。事后那段"接回 cycle"保留为兜底。
- 既有合同不被本修改变：World/BODY 的首步由本轮 Decision 绑定的 `step_spec_override`
  构造，`ActivityAdoptionSpec.initial_executable_step_spec` 在该路径上本来就是 `None`；
  首步是否真的构造改用 AG-2 该 Activity 的 `executable_step_refs` / `action_refs` 与
  runtime 的真实 Action/attempt 证明，不为了填这个字段去改 `components/`。
- `run` 新增 `--capture-candidates`（默认关闭）的实验观测：每 phase 写
  `g1-candidate-trace-<phase>.jsonl`，分 `CANDIDATE_SET_MATERIALIZED` / `REQUEST_LEGAL_SET` /
  `PROMPT_FINAL` / `PROMPT_BUILT_NOT_SENT` / `POST_TICK_SNAPSHOT` / `ROUND` / `EVENT` 七类行。
  生成、排序与最终呈现**分开取同一次调用已经产生的结构**：材料化集合按本请求的
  `candidate_set_id` 从既有 AG-0 materializer 只读回同一对象；最终候选只取 `build_prompt`
  同一次返回的 payload，不二次构造、不解析 prompt 全文。材料化阶段明确标 scope
  （per-source 25 → 去重 → 字典序 → 全局 100 之后，不是原始 discover 全量）；拿不到就写
  `UNKNOWN`/`NO_STRUCTURED_PROMPT`。只落固定枚举/已知 ID 形状/布尔/计数/顺序号；默认关不建
  文件、不做额外状态读取；观测写失败只记有界异常类型名，不改变候选、排序、资格门、预算或
  Decision。
- 发送状态与结构化 payload 分开：正常返回只记 `send_status=UNKNOWN`（拿到响应不等于证明
  实际网络发送，也可能来自重放）；只有预算原因码或 `RequestNotSentError` 这类**发送前明确
  拒绝**才写 `NOT_SENT`。普通异常可能是发送后超时，同样写 `UNKNOWN`，`sent` 为 `None`。
- 原 tick 之后另写一行 `POST_TICK_SNAPSHOT`（明确 `POST_TICK_READ`，不是发送前原子快照）：
  从既有 `activity_read.list_open_activities()` 取 PAUSED Activity IDs，从正式
  `decision_read.get_decision()` 取实际提交的 `reevaluation_hint_ref`，从
  `waiting_coordinator.list_conditions/list_eligibilities` 取条件/资格 id 与状态。只读，
  不 maintain/reconcile/算资格/写 owner；读失败写 `UNKNOWN`/`ERROR`，不当 EMPTY。

- 第五批 **e 不是 G1 批**（2026-10-31 本人直接批准新隔离批次/新进程树临时 canonical/
  20 请求 1800 真秒/失败不退不续跑）：[预登记](../../../../docs/engineering/world-observe-e-prereg-20261031.md)。
  身份 root `/root/nyairo-world-observe-20261031-e`、独立 authority `…-e-memory-authority`、
  `run:world-observe-20261031-e`、域 `world-observe:20261031-e`、历史 148、截止
  `2026-11-01T01:00:00+08:00`。与 d 共用单 `natural` phase，但 World gate 在本进程树的
  **三处环境映射**里显式 `canonical`：`_runtime_env`、phase 子进程 env、自有 World child env；
  d 仍是显式 `off`，a/b/c 不进入自然路径。
- e 的 **World 写面隔离**（2026-10-31 收尾）：既有 bridge 的路径顺序是
  「显式 env → `CHIYO_WORLD_HOME`/`HERMES_HOME` → 仓库默认 `./data/world/...`」，
  其中 `world_action_lifecycle.default_path()`、`world_activity`、`world_experience`、
  `world_self_maintenance` 都有这条回退链，`intent_executor` 的 registry 还直接读
  `os.environ`。修法只动 scripts 实验层：新增 `_world_isolation_env()`，把 home、registry、
  lifecycle 与三个 bridge 存储路径一起钉到本实例的 `<world_home>/run`；
  `_phase_child_env()`（phase 子进程 env）与 `_runtime_env()`（runtime/assembly/adapter 的
  mapping）共用它。World child 的 env 由 `demo.owner_children` 从 phase 子进程的
  `os.environ` 继承构造，因此也自然落在同一 root；父进程 `os.environ` 全程不写。
- phase 报告新增安全字段 `isolation`：在**子进程自己的** `os.environ` 里读 gate/home/
  registry/lifecycle，只报路径与固定枚举；跑完补 `lifecycle_exists` 与本根 ledger 的有界
  记录（state/execution_id/verb），并给出仓库默认 lifecycle 路径供父级前后快照比较。
  不输出整份 env、不读仓库默认文件。
- e 的验收不再 `mock.patch.dict(os.environ)`：canonical 正控通过真实 phase 子进程
  （现成 `_phase_child_env` + `subprocess`）跑正式 AG-1→AG-2→AR-0；e 的成功动作以
  同 Action 的 attempt/receipt、`provider_operation_ref` 对应的本根 lifecycle `APPLIED`
  记录、以及 verb 级 owner 状态（POSE 姿态/朝向、MOVE 目标）配对，不用
  「receipt 非空」或「journeyChanged」兜底。d 的 GATE_OFF 也放进真实子进程（固定 latch，
  因为 d 的绝对截止已过，父级 `start_run` 按设计拒开窗口；这是显式夹具，不是真实 run）。
- 仓库根 `data/world/run/action_lifecycle.jsonl` 的历史归属：46 条记录里 2026-10-31 的 12 条
  全是 `origin=life_ar0` 的 POSE 生命周期（CREATED/ABORTED），时间 01:53–02:00Z（09:53–10:00 CST）
  对应失败 worker 的 in-process World 求值。**直接证据**是这些行的 origin/verb/state 与
  `default_path()` 的回退条件；**推断**是「具体哪几条来自 `.tmp_probe_e.py`、哪几条来自
  in-process 测试」——同一路径同一 schema，逐条归属记 `UNKNOWN`，不拿新绿抹掉。

## Alternatives considered

- **在 `g1_same_work_recovery_demo.py` 里直接换成真实 client**：最强的理由是那份 demo 已经
  28 项全过、代码路径被验证过，改一个 client 最省事。否决原因：它仍然用脚本选候选、脚本写
  内容，真实模型只当"摆设"，得到的绿恰恰是计划要排除的"表面挂真 client、实际脚本决定"。
- **让真实 run 复用离线替身，只用一个环境变量切换**：最强的理由是能保证六项全覆盖、结果好看。
  否决原因：那是一次演示而不是采用；把替身留在真实入口里，等于用假模型冒认采用证据。
- **预选候选 / 固定 `work_artifact_id` 来把序列做成确定的**：最强的理由是能稳稳走到
  UNKNOWN/CAS/NO_ACTION；否决原因：计划明确不许悄悄改候选合法集，改了之后问的就不再是
  "模型会不会选"，而是"脚本让它选"。
- **用显式 trigger 循环（检查窗口设 0）尽快问完**：最强的理由是在 1800 秒里最大化覆盖机会。
  否决原因是这就是"换 trigger 循环骗认知"；改为固定的 1 分钟实例参数，让 runner 自己的
  检查窗口合法地多次到期，模型仍可什么都不做。
- **把模块级 `DEADLINE` 直接全局改成 c 的 01:00**：最强的理由是一行改动、所有运行期读取点
  自动生效。否决原因是它同时把 a/b 的历史截止从 17:00 延到次日 01:00，等于用一个新批次的
  授权顺手放宽旧批；改为 per-profile 的 `deadline` 字段，`DEADLINE` 只作 a 的兼容常量。
- **版本比较写成「反复剥掉已知前缀再比字符串」，成果判定只看非空 + 排除项**：最强的理由是代码
  最短、能兼容所有历史拼写，也不用把 owner 读回的对象身份带进来。否决原因是它会把
  `artifactversion:artifactversion:<hex>`、裸 hex 这类未定义写法也认成同一版本，而且不绑对象
  身份时，另一件作品的 head 只要值相同就能确认——本批离线红正是这条：`another-work` 的负控
  返回了 True。改为只认三种被证明的形状 + receipt/owner 对象身份配对。
- **把 d 直接放进 `BATCH_PROFILES`，顺手改旧的两条枚举断言**：最强的理由是一张表就够了，
  不用新增 `MODE_PROFILES`/`BATCH_CHOICES`，CLI 也自动带上 d。否决原因：那两条断言记录的
  是历史三批的封闭范围，改旧测试等于让"d 是第四批 G1"混进旧身份；而独立注册能同时满足
  "旧集合原样"和"CLI 允许 d"。实际也验证了旧文件不用动。
- **直接改 `components/life/scripts/autonomous_life_assembly.py`，把 cycle 构造移到
  `resolve_world_provider` 之后**：最强的理由是这是根因，生产路径都受益。否决原因：本单
  写集不含 `components/`，那属于跨组件合同改动；先在 scripts 层把同一个已读回 READY 的
  provider 接回 cycle，只对 d 生效，不改 a/b/c 行为，等有独立授权再决定是否上移。
- **在 d 的 phase 里自己填 `world_ready=True` 或注入候选**：最强的理由是这样离线测试最稳定、
  最快出样本。否决原因：这正是任务书禁止的 mock；要证明的恰恰是"真实 World 起来并读回
  READY、真实候选进入 AG-0"，伪造布尔或候选会把整条观测证据作废。
- **只把感知 provider 接回 cycle，仍让 `world_action_adapter` 为 None**：最强的理由是这正是
  收尾时已交付的接线，世界机会已经能进 AG-0，看起来"缺的是候选"已修好。否决原因：cycle 的
  `_world_step_enabled` 同时要求 provider 与 adapter，缺一半时 START 只建 Activity、首步/
  Action 不构造；原 d 报告里三件空 step/action refs 就是这个状态。补法必须两半一起，且用
  resident 已有的标准解析/构造口，不另造 adapter 或 owner。
- **在 `components/` 里给 `ActivityAdoptionSpec.initial_executable_step_spec` 填上世界首步**：
  最强的理由是任务书点名要看这个字段非空，填上它是最直观的"首步存在"。否决原因：
  World/BODY 的首步本来由本轮 Decision 绑定的 `step_spec_override` 构造，该字段在这条路径
  上恒为 None；为了让一个不承载首步的字段变真去改组件合同，既超写集，也把"证明首步"换成
  "让字段好看"。改用 AG-2 的 step/action refs 与真实 Action/attempt 作等价证据。

- **在测试进程里 `mock.patch.dict(os.environ)` 模拟 canonical/子进程 env**：最强的理由是
  最省事，可以直接在父进程构造 runtime 并调用 `evaluate_once`，还能顺手模拟父 shell 的
  canonical。否决原因：这正是要证明的东西——`intent_executor`/`world_action_lifecycle` 读的是
  **执行进程的** `os.environ`，父进程 patch 出来的值既不是真实 phase 子进程的映射，也会让
  in-process 求值把 World 写面回落到仓库默认（这正是 10-31 那 12 条 POSE 记录的来源）。
  改为真实子进程 + `_phase_child_env`，父进程环境前后原样。
- **直接删掉/截断仓库根 `data/world/run/action_lifecycle.jsonl` 或 `.tmp_probe_e.py`**：
  最强的理由是现场干净、零新增一定成立。否决原因：它们是父级要求保留的偏差证据，删除或
  回滚会把「哪个写口造成的」这一点一起抹掉；本单只证明新路径不再写入，旧文件原样保留。
- **给 `components/` 的 bridge `default_path()` 加 fail-closed（缺 home 就拒绝写）**：最强的
  理由是能从根上堵住仓库默认回退，所有调用者受益。否决原因：那属于跨组件合同/生产默认改动，
  本单写集不含 `components/`；现有显式 env 机制已足够把本批三处映射钉死，先不动承重墙。

## Consequences

- d 的代价与边界：多一个**非 G1** 身份与一条独立 phase，多两个布尔字段（只在该 phase 的
  trace 行）。两个布尔只证明那两句文本出现在交给模型的最终结构里，不证明发送、送达或理解；
  `send_status` 仍是 `UNKNOWN`（拿到结构化 payload 不等于证明真实网络发送）。真实 run 里
  没有 PAUSE 决策点就整批 `NOT_COVERED`，不判文案效果。
  收尾后的现实结果：d 的 natural phase 起标准 World 后，`assembled["world"].status` 是
  `READY`（provider 自己 `get_status()` 读回），四类世界机会（stay/pose 等）经 AG-0 进入
  正式请求——离线两轮请求里 `world_candidates_per_request=[4,4]`，不再停留在
  `NO_MATERIAL`。这解决的是"该装配无候选"的接线缺口。首步缺口另修后（见 Decision）：
  离线真 owner 链的第一轮 START 选中的 World BODY 候选会真的构造 step 与 Action，
  gate off 下该 Action 停在 `PREPARED`、attempt 为 `PRE_SUBMIT_FAILED`/`GATE_OFF`、
  零回执、身体姿态不变；这不是"有候选"，而是首步真的走到了 AR-0 的明确拒绝。
  两者都不等于真实运行时刻一定有候选：离线夹具时钟是"当前 + 400 分钟/轮"，不代表真实
  时点；真实模型是否在这种材料下 PAUSE 仍待真实 run，未出现就 `NOT_COVERED`。
  此外仍不注入 operator 材料/任务/对照，也不改世界生成机会。
- 旧测试不再红：`tests/test_g1_real_adoption.py` 与 `tests/test_g1_batch_c.py` 里硬编码
  `set(BATCH_PROFILES) == {"a","b","c"}` 的断言，在 d 改为独立注册后按原文件原样通过
  （已实跑 `tests.test_g1_batch_c` 的两条身份断言）；旧测试文件未改。
- **收益**：真实模型的每一次选择、每一段内容都进正式链；operator 的两个干预点在明面上且
  各只一次；预算、窗口、started 的持久化都能被 audit 直接核对。离线检查覆盖了共享 cap、
  发送前截止/失败门、UNKNOWN 只对原 operation、CAS 真拒、prepare/run 开关与停 owner。
- **代价与上限**：真实 run 是有条件地覆盖后半段。既有 reconsideration 账本会按 6 小时 TTL
  记住 NO_ACTION，而真实窗口只有 30 分钟，所以「NO_ACTION 之后再续一次」是否自然出现取决于
  这次 run 的合法窗口与模型选择，可能不会出现；这是未测概率，不作大概率判断，也不为凑覆盖
  解除或跳 TTL。出现即记，不出现就记 `NOT_COVERED`。单次 run 最多 20 次预留，失败的也不退。
- 离线用夹具时刻直接走正式 `evaluate_once`，不是自然等待；它证明链路与门，不证明模型行为。
- 版本等价与身份配对只在本入口的比较层：组件/生产规则、已落盘的旧 refs、旧报告和旧 payload
  都没改；这里修好离线识别，不等于已还原本批真实 run 首成果识别失败的历史因果，产品失败细因
  仍未知，本批仍按 `NOT_COVERED` 记，不补造 pause/P2，也不动 101/300。
- 生产业务代码零改动；新增的只是一个入口脚本、对应测试、runbook 与本笔记。
- 候选观测的代价与上限：观测文件是实验副本，不是全量证据。per-source cap 之前的 discover
  全量只能是 `UNKNOWN`；三段之间只记集合差异，不推裁剪或拒绝原因。它回答「这次请求实际
  看到哪些候选、顺序如何」，不回答「为什么没被选」。每轮 `ROUND` 行只在模型真的枚举且
  当轮确有请求时标 `model_decision_present`，流程 NO_ACTION / QUIET / NOT_DUE 不当模型选择。
  `pause_condition.condition_ref` 是被提供的条件；实际提交的 `reevaluation_hint_ref` 与
  条件/资格状态改由原 tick 后的只读旁证记录，仍不证明自然资格生成或自然采用。
- 批次身份适配的代价：`prereg`/`batch` 落盘多一个 `batch` 字段，phase config 多带
  batch/run/域；旧批 a 的 prereg 若没有 `batch` 字段仍按 a 读回，其它缺字段一律拒绝。
  两批仍共用同一份代码与 `REQUEST_CAP`，没有新的额度、模型或行为路线。
- c 的代价与上限：多一个封闭身份和一个 per-profile 截止字段；prereg/config 现在**必须**
  带与所选批次一致的截止（现有 a/b 的记录本来就写了 17:00，没有需要迁移的旧记录）。
  20 次预留仍是整批共享。新增 `tests/test_g1_batch_c.py`：profile 边界在进程内测，
  `offline-check --batch c` 走真实 CLI 子进程（真 owner、脚本 transport、HTTP 硬拒），
  另跑一次 capture on 的离线链；不重跑 a/b 历史 offline-check 与旧 trace 模块。

- e 的代价与边界：多一个封闭身份、一处 `_world_isolation_env` 与一个 `isolation` 安全块；
  三处映射一致由真实子进程自证，父进程环境前后不变。仓库默认写面在本模块前后零新增
  （存在/大小/mtime_ns 三项比较），但这只覆盖 `natural` phase 子进程路径：旧测试
  `tests/test_pause_observe_d.py::DWorldStepAssemblyTest` 仍在**父测试进程里**直接
  `evaluate_once`，单独运行它仍可能写仓库默认台账（写集冻结，本单不改旧测试）。`.tmp_probe_e.py`
  与那 12 条历史记录原样保留，逐条归属 `UNKNOWN`；不把它们算成本单的新绿。
- e 的门只在三处 env 映射里显式给出，不从父 shell 继承、不写 `.env`/实例默认；父进程
  环境消失不等于真实 root 的许可已撤，真实 run 的后果与撤销边界仍按预登记。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g1_real_adoption.py offline-check`
  → 37 项 CHECK 全 PASS、`RESULT: PASS`、exit 0；含 `tests/test_g1_real_adoption.py` 31 例。
  新增的是版本 ref 严格等价与 owner 重读对象身份配对：真实 runner 的
  `extra.owner_version_id → _safe_wake → P1` 正控仍过；错作品/错 Activity、同 head 但读的是
  另一件作品、未定义包装与裸 hex 都失败关闭。
- `offline-check --batch b` 同一命令同样 37 项全 PASS、exit 0：b 的 prereg/batch/window、
  p1/p2 config 与两份 phase 报告全部落在 `run:g1-real-20261030-b` / `g1:real:20261030-b`，
  两个不同 PID 共用同一本 b 账本 20 次；a 默认映射、未知批、另一批 root、错批/缺身份
  prereg、预存 authority 的拒绝都在同一次检查里。
- 反例已覆盖：不同 Activity/别的作品即便 A 变版也不推进、对账未确证不算已对账、任意
  非 CONFLICT 的 FAILED 与未落 owner 的并发写都不算 CAS、`content.complete` 抛异常后第二次
  发送被挡、P1 落盘停因挡住 P2 新锁；原正式 owner 两 PID 正链仍过。
- 未调用任何产品模型、未访问旧实例、未 prepare/run 真实根；离线 scratch 全部保留。
- 第三批 c：`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v
  tests.test_g1_batch_c` → 10 例 OK、exit 0、123.576 秒（WSL）。覆盖 c 映射与历史 121；
  a/b 截止仍是 17:00、模块级 `DEADLINE` 仍是 a；窗口按批取截止（长窗口时落盘值等于该批
  截止）；错批 root（双向）、错/缺截止的 prereg、错 deadline 或错 run 的 phase config、
  预存 authority、同根重复 run 全部拒绝；`offline-check --batch c` 子进程 37 项 CHECK
  全 PASS；capture on 的 c 链 p1/p2 都有 `CANDIDATE_SET_MATERIALIZED`/`ROUND`/
  `POST_TICK_SNAPSHOT`，snapshot 标 `POST_TICK_READ`、`not_pre_send_atomic`，观测文件
  不含材料正文。未真实 prepare/run c、未调用产品模型、未访问 `/root` 或旧实例，
  scratch 全保留；产品 121/300 不变。
- 第四批 d（非 G1，含收尾）：`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B
  -m unittest -v tests.test_pause_observe_d` → **19 例 OK、约 39 秒**（WSL）。覆盖
  d 身份/截止/历史 141、独立注册（旧集合仍 `{"a","b","c"}`、`MODE_PROFILES`/`BATCH_CHOICES`
  含 d）、三向错批 root、错截止 prereg、`natural`/`p1` 两个方向的 phase 拒绝、cap 20 停发、
  同根不重跑；`prepare` 里 `_provision_operator_material` 与自然 phase 里
  `SubmissionInjector` 都被打成 raise 且未被调用；两布尔的
  exact/factonly/neither/missing 与"同行 send_status + 不落文本"；父级真实入口（子进程）
  跑完一个自然 phase。收尾后自然链的实际观测：标准 World child 起来、
  `world.status="READY"`、`world.gate="off"`、`world_candidates_per_request=[4,4]`，
  两行 `PROMPT_FINAL` 的 `send_status="UNKNOWN"`、布尔分别为 `true/false` 与 `true/true`，
  World 停止后 `socket_live_after=false`；`coverage={}`，`result=OBSERVED`。
  另实跑 `tests.test_g1_batch_c` 的两条身份/截止断言，按原文件通过（旧文件未改）。
  未 prepare/run 真实 d 根、未调用产品模型、未访问 `/root`。
- 首步装配修复（2026-10-30）：同一命令 `... -m unittest -v tests.test_pause_observe_d`
  → **21 例 OK、exit 0、约 42 秒**（原 19 例断言全部保留，新增 `DWorldStepAssemblyTest`
  2 例）。新增链在 d 的离线真 owner/Supply/World 夹具上跑一次正式 `evaluate_once`：
  Decision 是明确标注的测试替身（只从真实 `WORLD_OPPORTUNITY` 候选里挑），断言同一
  Activity 的 AG-2 `executable_step_refs`/`action_refs` 非空、adoption spec 是 BODY
  Activity、runtime 里恰有一个 Action、attempt 为 `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执、
  身体姿态不变。`ActivityAdoptionSpec.initial_executable_step_spec` 在该路径按既有合同是
  None（首步走 `step_spec_override`），测试显式记录这一点，没有为此改 `components/`。
  未真实 prepare/run d 根、未调用产品模型。
- 候选观测：`tests/test_g1_candidate_trace.py` 32 例在 WSL 实跑 OK（约 99 秒）。覆盖固定
  枚举/已知 ID 形状（任意 ASCII 诱饵、伪造前缀不再合法）、诱饵私文与异常原文不落盘、
  非法 refs 保留实际 total 并计 unknown、缺/坏 candidate set 写 `UNKNOWN`/`ERROR`、观测副本
  上限、默认关不产文件且不读状态、on/off 内层调用与额度等价、仅发送前明确拒绝记
  `NOT_SENT`、普通异常/正常返回写 `UNKNOWN`（`sent=None`）、无结构化 prompt 不重建、16 条
  上限不补齐、真实 `build_prompt` 的 12000 字符结构裁剪被同一份 shape 观测到、
  `POST_TICK_SNAPSHOT` 只读 PAUSED/条件/资格/hint 且读失败写 ERROR；最小真实接线冒烟复用
  既有 `prepare` + `start_run(offline=True)`，同一 scratch 跑 on/off 两次，p1/p2 阶段齐全、
  候选顺序保持、最终 prompt 是请求集合的有序子集、覆盖结果与账本等价。
- e 隔离收尾（2026-10-31）：
  `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v
  tests.test_world_observe_e` → **21 例 OK、exit 0、约 59–62 秒**（WSL）。覆盖：第五身份与
  旧 a/b/c/d 枚举不动、per-batch gate（真实子进程里父 canonical 不进 d）、三向错批/错截止
  与 phase 拒绝、cap 20 停发、摘要只认 e 根；e 的真实 phase 子进程（guarded 与父级
  `start_run` 各一次）准备零材料/零 injector、World READY/`gate=canonical`、正式
  AG-1→AG-2→AR-0 一个 Action SUCCEEDED，receipt 的 `provider_operation_ref` 与本根
  lifecycle `APPLIED` 同执行配对、POSE 姿态/朝向与 target 一致；d 的真实子进程在显式 off
  下 Action `PREPARED`、attempt `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执、本根 lifecycle
  只有 CREATED/ABORTED；`isolation` 块证明 gate/home/registry/lifecycle 都在新根，
  hostile 父环境（gate/home/registry 全指向仓库默认）也被覆盖，父进程四项环境前后原样；
  仓库默认 5 个世界写面存在/大小/mtime_ns 前后零变化（`action_lifecycle.jsonl` 仍
  18584 bytes、mtime_ns 1793412031）。未 prepare/run 真实 e 根、未调用产品模型、
  未 source 密钥；产品 148/300 不变，`.tmp_probe_e.py` 与历史台账原样保留。
- e 路径守卫收尾（2026-10-31）：同一命令
  `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v
  tests.test_world_observe_e` → **28 例 OK、exit 0、79.357 秒**（WSL），原 21 例主链
  断言一个未削。新增 7 例负控覆盖：实例缺 `world_home`/`instance.json` 不可读、相对
  `world_home`、绑定解析到根外目录、根内符号链接逃逸，全部在 `start_run` 写窗口/started
  之前返回 `WORLD_HOME_UNAVAILABLE` 或 `WORLD_HOME_OUTSIDE_ROOT`；坏 home 时带 hostile
  父环境（home/registry/lifecycle 指向仓库默认）的真实子进程也不会借用，拒绝原因如实
  返回；phase 子进程在 `cmd_phase` 进 runner 前自查 7 个写面，单项 `CHIYO_WORLD_HOME`
  错配即 `WORLD_CHILD_ENV_MISMATCH`/exit 2、不落 phase 报告（World/Supply 未起），
  正确映射的自查返回 OK。实现只给 e 加严：`d` 保持 b6 交付时的宽松投影（其离线测试
  子进程仍靠这份 env 隔离），a/b/c 路由不变；守卫只读绑定，不创建目录、不改
  `components/`、不 patch 父进程 `os.environ`，全部临时根保留。

- 发行脱敏参数同步（2026-11-02）：`g1_real_adoption.MODEL_BASE_URL` 不再内置真实
  网关地址，默认空串；真实入口只认运行期 `NYA02_MODEL_BASE_URL`，缺失时 `prepare`
  在建实例/写 prereg 前、`start_run` 在写 started/窗口前返回 `NO_MODEL_CONFIG`。
  `_verify_model_config` 仍严格比对 provider 与 `cn:deepseek-v4.1-flash`，但地址以
  显式 env 为准；离线夹具继续用 `https://offline.invalid/v1` 的合成值，phase 子进程
  在离线时剥掉父 shell 的 `NYA02_MODEL_*`。原决定（真实 run 由模型自选、参数一致才
  发送、失败不退）不变；新增风险是真实入口现在必须由调用方显式设置地址，仓库内不再
  保留可自证的默认值。验证：`tests/test_release_model_address_redaction.py` 9 例离线
  PASS（缺参前拒/显式合成地址/离线不继承生产 env/父子 env/prepare 写前拒绝）。
