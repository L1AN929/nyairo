# Agent Note: 运行恢复先于新动作，短事务与网络不持锁（INFRA F161～F167）

Status: implemented

## Problem

设计 VIII.13 / Q14–Q45 要求：owner 提交版本/operation mapping/receipt 先于 Action 成功；
崩溃后按原 operation 对账，不凭 checkpoint 新造 key，也不盲重做；I/O 与模型调用不在事务锁内；
备份恢复必须先取备份之外的新 writer epoch 与至少同新的删除高水位，不能把 normal restart
称作 backup restore。仓库里这些机制分散在 AG-1 `reconcile_after_crash`/WAL、AR-0
`recover()`/`reconcile_action()`、AG-2 `recover_all_and_reconcile`、LR-2 lease 与 Supply
`resolve_operation`，但没有一个用户可读入口回答：“这个实例现在能不能装配？要不要走已有
恢复入口？还是必须人工先停写者？” 直接看文件存在与否会得出两个错误结论：WAL 文件在就一直
拒；进程不在就以为 lease 已释放。

另一件必须先说清的事：AG-1 与 AG-2 integration store 在同一个 state root 下共用文件名
`commit_intent.json`，仅靠文件名无法判断是谁的未完成提交；但两者的 `schema_version` 不同，
可以按 schema 认领，再与各自 journal 行数比较。

## Decision

1. **新增纯读 `components/life/scripts/runtime_recovery_review.py` + `scripts/infra_runtime_recovery_cli.py`**：
   - 只读 instance.json、authority marker、WAL（按 schema 认领 + `store_revision` 与 journal
     行数比较）、AR-0 快照与 `open_submission_intents`，以及（可选）Supply owner 的
     `lookup_operation` 只读查询；
   - 分类 `ASSEMBLABLE` / `NEEDS_EXISTING_RECOVERY` / `MANUAL` / `READ_BLOCKED` / `UNKNOWN`，
     并给具名下一合法入口；
   - **不取 flock、不调用 `load_state`（会重放 WAL）、不建目录、不发模型请求**。
   - marker + live pid 只能证明“被持有”，不能证明“空闲”；未探测 flock 写入 `unevaluated`。
2. **正常重启 / 崩溃恢复全部复用现有入口**：演示用独立子进程重建真实 runtime；崩溃点在
   适配器真实的 `response_lost_after_write`（owner 写入已落、答复丢失）用 SIGKILL 杀死；
   同根新进程调用 `LifeIntegrationCoordinator.recover_all_and_reconcile(
   auto_reconcile_unknown_actions=True)`，AR-0 把 open fence 转 UNKNOWN 后按原 operation
   reconcile，版本数不增。
3. **F164 只验既有 CAS/源核验**：旧 `expected_head` 被 Supply owner 拒（CONFLICT，零新版本）；
   已 SUCCEEDED 的 Action 再次提交是 `idempotent_replay`，适配器 0 次调用。跨 owner 乱序
   dependency buffer 在仓库中不存在，未新造，缺口见 `docs/engineering/infra-runtime-recovery-options-20261027.md`。
4. **I/O 不持事务锁的具名事实**：AG-1/Integration 的 `_io_lock` 目前只有定义、未被调用；
   AR-0 依赖 lease + capability，不另持文件锁。演示在认知调用阻塞时，只读 CLI 与真实
   Supply owner 读都可继续，并对 AG-1 `run/.agency_decision_io.lock` 做非阻塞写锁探测。
   LR-2 lease 在进程生命周期内一直持有，它是 writer fence，不是事务锁。
5. **F165/F166 如实报缺口**：没有正式备份恢复/迁移入口，CLI 报 `NOT_IMPLEMENTED`，
   本单不复制、不恢复、不迁移任何数据库。

## Alternatives considered

- **只看 WAL 文件是否存在就拒绝启动**：最简单，但会把一个已经写入 journal、只差快照的提交
  永久判成危险；也会把 integration 的 WAL 误算到 AG-1 头上。
- **为了读状态直接调用 `store.load_state()`**：会把 WAL 重放掉，只读检查变成一次写；
  失败时还会删掉无法解析的 WAL，掩盖证据。
- **在只读检查里探测 flock（acquire+release）**：能直接回答“是否空闲”，但会短暂持有写者锁，
  与“只读不触 lease”冲突，也会在别的进程正要提交时制造竞争；因此只读侧不取锁，明确写
  `UNKNOWN`/未评估。
- **为乱序事件新建 buffer owner**：超出本单写集，且需要新持久格式与时限策略；先保留缺口。

## Consequences

- **收益**：启动前能用一条命令知道该正常起、该走既有恢复、该停别的写者，还是该人工查文件；
  崩溃恢复走原 operation，不会写第二次；只读检查不会改变实例。
- **代价与已知上限**：writer 是否真正空闲仍未被证明（只据 marker+pid）；进程扫描只是本进程
  视角；模型配置只描述不验证；owner operation 查询需要显式给 socket/subject；没有跨 owner
  乱序 buffer，没有备份恢复/迁移入口；状态文件是 JSON+journal+WAL，不是单 SQLite reference
  profile，不宣称 T1 全原子。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/infra_runtime_recovery_demo.py`
  → 25 项 CHECK 全 PASS，`ALL_EXPECTATIONS_MET`，exit 0；隔离根与 `record.json` 无论成功失败
  都保留，演示不删除任何已产出数据（`finally` 里原 `shutil.rmtree` 已移除）。
- `python -B -m unittest tests.test_infra_runtime_recovery` → 11 例 OK（含输出不回显模型 key）。
- 演示覆盖：只读分类、真实 START、不同 PID 同根重启无重放、SIGKILL 到 hook、同根恢复版本
  不增、旧 revision 拒写、成功动作幂等回放、阻塞 I/O 时只读与 owner 读继续、writer 冲突拒装配、
  备份/迁移缺口。Decision/内容为脚本替身；不证明真实模型自然采用、G1、24h 或完整 crash 矩阵。
- **偏差（保留，不抹掉）**：首轮 demo 在 `finally` 中于全部检查通过后 `shutil.rmtree` 隔离根，
  违反“不删数据”；被删除的那次运行产物不可恢复，本 note 不倒写成未发生。返修只移除该删除，
  并把失败路径的 `record` 也落盘；成功/失败均保留根与记录。

## 后续（2026-10-29）：把恢复接到用户可调用的 CLI

**Problem**：上面的恢复只在演示脚本里被证明，用户手上仍然只有只读 review；一次“作品已落地、
回执未知”的重启之后，没有一个正式入口可以让新进程按原 operation 对账并继续同一作品。

**Decision**：在同一个 `infra_runtime_recovery_cli.py` 上增加显式 `--recover`（默认仍纯读）：

```
python3 scripts/infra_runtime_recovery_cli.py --instance <root>            # 只读复核
python3 scripts/infra_runtime_recovery_cli.py --instance <root> --recover  # 显式恢复
```

- 恢复逻辑放在 `runtime_recovery_review.py` 的新函数 `recover()` 里；`review()` 仍是纯读，
  导入模块不产生副作用。
- 绑定全部来自实例自身（`supply_socket`/`subject_id`/`workspace_id`/`artifact_grant_id`，
  Memory 只读 `NYA02_MEMORY_*`）；缺绑定或只读预检为 MANUAL/READ_BLOCKED 时在装配前具名
  `BLOCKED`，不取租约、不写。
- 实际装配真实 `ProductionLifeRuntime`（取得正规 lease）、同一真实 Supply adapter 与
  `recover_all_and_reconcile(auto_reconcile_unknown_actions=True)`；之后对仍为 UNKNOWN 的
  action 逐个调用 AR-0 自己的 `reconcile_action`。**只按原 operation 对账**：不 submit、
  不换 id、不重放旧 Decision/成功动作、不调用模型；`finally` 里 `runtime.close()` 正规释放。
- 输出只列 action/operation id、`confirmed`/`still_unknown`/`blocked`、结果与后果投递计数、
  Memory disposition 计数和 adapter 提交次数；不回显 owner 对象、正文、prompt、key、env。
- 退出码：0 = RECOVERED/NOTHING_TO_DO，3 = BLOCKED，4 = 仍有 UNKNOWN。

**Alternatives considered**：另起一套恢复协议或新 owner —— 否决，等于把一个已验证的
`resolve_operation`/`reconcile_action` 换成未经审查的新协议；在 `review()` 里暗中写 ——
否决，会破坏只读入口的全部保证；恢复失败时自动重试/换 key —— 否决，UNKNOWN 只能按原
operation 对账，重试策略需要单独合同。

**Consequences**：用户现在可以用一条命令恢复并看到“哪些已确认、哪些仍未知、哪些被拒”；
恢复仍不把回执当 Activity 完成或 Memory 已记，handoff/settlement 只报 owner/consumer 的实际答复。
仍未覆盖：完整 crash 矩阵、备份恢复/迁移、跨 owner 乱序 buffer；Decision/内容仍是脚本替身。

**Verification（2026-10-29）**：
`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/life_unresolved_recovery_demo.py`
→ 8 项 CHECK 全 PASS、`RESULT: PASS`、exit 0；write/recover/continue/recover2 四个不同 PID，
丢回执那次版本只增一次，恢复后版本 3→3、adapter 提交 0、Activity/预算不变、再续写 A 到 4 版、
B 不动、二次恢复 `NOTHING_TO_DO`。`python -B -m unittest tests.test_life_unresolved_recovery
tests.test_infra_runtime_recovery` → 15 例 OK（默认只读、缺绑定 BLOCKED、owner 离线
STILL_UNKNOWN 且零提交、真实写者占租约 PRECHECK_MANUAL 零写、owner 恢复后版本仍只有一次、
原只读 11 例不回归）。恢复后 handoff 交付 0、outbox 待投递 1、Memory accepted 0：回执不等于
已入 M0，如实报告。

## 后续（2026-10-29）：恢复之后的后果按域承接

**Problem**：上一步只证明“按原 operation 对账确认成功”，但恢复后的后果仍只报
`outbox 待投递 1 / handoff 交付 0 / Memory accepted 0`，容易被读成“恢复没把后果处理完”。
需要区分：LR-5 是否真的消费了这次结算；`memory/goal/world` 的 pending 是漏投还是生产
策略拒写；AG2 经历交接为什么是 0；以及能不能因为 pending 就去写 M0。

**Decision**：
- `RefusingSettlementConsumer` 不再继承 `FakeMemoryConsumer` 的硬编码
  `consumer_domain="memory"`，而是用构造时校验过的自己的域名注册。此前 goal/world 在
  `register_consumer` 时互相覆盖成 memory，AR-1 只剩 life+memory 两条记录；修复后
  life/memory/goal/world 四域各自留下具名 `ProductionExecutionRefused` 拒写记录。
- `runtime_recovery_review.recover()` 增加只读的 `consequence_domains`：逐事件列四域
  delivery（status/disposition/last_error_ref）、LR-5 是否消费该 settlement（经
  `ActivityProgressReadService.list_consumed_settlement_refs` 核对）、既有/本次 AG2 交接、
  Memory 准入与 M0 关系；`pending_reason` 明确“策略拒绝域保持 pending，不是漏投”。
- `infra_runtime_recovery_cli.py --recover` 的文本输出同步分域显示；默认只读 review 不变，
  不自动起认知/模型，也不因为 pending 自动向 M0 写。
- `ActivityProgressReadService.list_consumed_settlement_refs()` 只读取已消费 settlement
  引用，不改任何进度/完成规则；F-167 的严格 source binding 原样保留。

**Alternatives considered**：把 `pending_outbox` 清零作为“后果已处理”的目标 —— 否决，
那会逼着把策略拒写的 memory/goal/world 改成假 ACCEPT，正是本仓库明令禁止的假绿；
为每个成功版本强造 SIGNIFICANT_PROGRESS 交接 —— 否决，Activity 仍 ACTIVE 时既有合同
不生成终态交接，造 handoff 只会把恢复证据变成自证；用 consumer 的 ACCEPT 计数当 M0
已记录 —— 否决，AR-1 memory 通知与 AG2 lexp 准入是两条不同事实，必须分开读。

**Consequences**：用户能用同一条 `--recover` 看到“life 已消费、memory/goal/world 策略
拒绝、outbox 待投递是设计保留、本次恢复没有新增 M0”。仍未覆盖：完整 crash 矩阵、
备份恢复/迁移、跨 owner 乱序 buffer；本用法里 Activity 仍 ACTIVE，没有可重投的经历交接，
所以“交接中途故障后重投”不造场景。Decision/内容仍是脚本替身，不证自然采用、G1、24h。

**Verification（2026-10-29）**：
`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/life_recovery_consequences_demo.py`
→ 11 项 CHECK 全 PASS、`RESULT: PASS`、exit 0；write/recover/repeat/continue 四个不同 PID，
恢复后 life 已消费 1 个结算，`policy_refused_domains=goal,memory,world`，outbox 待投递 1
且理由指向策略拒绝，既有 AG2 交接 1、本次投递 0，M0 记录 1→1（恢复与重复恢复都不增），
新 Decision 续 A、B 不动。`python -B -m unittest tests.test_life_recovery_consequences`
→ 3 例 OK（四域各自注册且拒写具名；life 消费绑定原 action/settlement/同 Activity；
非权威事件 IGNORE 且零 checkpoint/消费/Activity 变化）。原
`tests.test_life_unresolved_recovery` + `tests.test_infra_runtime_recovery` 15 例不回归。

## 后续（2026-10-29）：Action 已成功、原经历交接待重试的跨进程恢复

**Problem**：上一块的恢复只在有未决 Action 时才有事可做。真实故障还有一种：Action 已经
`SUCCEEDED`、AR-1 已结算、作品版本也在，但 AG-2 经历交接在真实 Memory consumer 前失败，
按既有合同留在 `pending_handoff_ids` 的 `FAILED_PENDING_RETRY`。此时 `recover()` 只看
Action，`rows` 为空就回答 `NOTHING_TO_DO`；实际上这次恢复已经重投了 handoff，或者仍
留着待重试交接，用户看到的是“无事可做”，把未完成的后果藏掉了。

**Decision**：
- `runtime_recovery_review` 新增 `_handoff_state()` 与 `_decide_status()`：把同一次恢复
  实际发生的 handoff 投递（dispatched/delivered/deferred/failed/pending ids）并入状态。
  `NOTHING_TO_DO` + 有 handoff 时，全部交付完 → `RECOVERED`；仍有 deferred/failed/pending
  → `PARTIAL`。`RECOVERED` + 仍有待重试 handoff 也降为 `PARTIAL`。没有 handoff 时完全
  沿用 Action 侧结论，不改已有语义。
- 投递仍只走既有 `recover_all_and_reconcile` 的第 3 步 `dispatch_experience_handoffs()`，
  不新增参数/表/owner，不凭最新作品重建 handoff，不改身份，不做自动重试。
- `infra_runtime_recovery_cli.py --recover` 文本输出列出待重试交接 id（白名单 id），
  退出码沿用：`RECOVERED/NOTHING_TO_DO`=0、`BLOCKED`=3、`PARTIAL/STILL_UNKNOWN`=4。
- 默认只读 review 不变，不暗写；Memory 未配置或写失败时交接保持原身份与状态，不造 ACCEPT。

**Alternatives considered**：只靠 `recover_all_and_reconcile` 的返回、让状态仍报
`NOTHING_TO_DO` —— 否决，用户会以为没有待办，正是本单要修的错误结论；把 `pending` 清零
当恢复完成 —— 否决，等于把 Memory 拒写改写成成功；为每次恢复新造一条 handoff —— 否决，
身份必须复用原 `lexp:result:`，M0 才能按原准入去重。

**Consequences**：用户在同一入口既能恢复未决 Action，也能恢复“Action 已成功但经历交接
待重试”的原 handoff；失败时如实显示 `PARTIAL` 与待重试 id，不报全部完成。仍不覆盖完整
crash 矩阵、备份恢复/迁移、跨 owner 乱序 buffer；不保证每条成功都入记忆。

**Verification（2026-10-29）**：
`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/life_recovery_handoff_demo.py`
→ 7 项 CHECK 全 PASS、`RESULT: PASS`、exit 0；write/recover-down/recover-up/recover-again/
verify 各自不同 PID。P1 两次真实写入、第二次 Action `SUCCEEDED`、原 handoff
`FAILED_PENDING_RETRY` 留在 pending；Memory 不可用时 `--recover` 返回 `PARTIAL`/exit 4 且
原 handoff 不变；Memory 恢复后同一命令 `RECOVERED`、交接 `ACCEPT`、M0 一条记录；再次恢复
`NOTHING_TO_DO`；版本 2→3→3、两次 attempt 均 1、Activity 不变、作品 B 不动。
`python -B -m unittest tests.test_life_recovery_handoff` → 6 例 OK。
