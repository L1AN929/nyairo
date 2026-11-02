# INFRA F161–F167：恢复路径与两个明确缺口（2026-10-27）

本页只记录本单核对到的事实和需要拍板的缺口，不是新协议提案。检查入口：

```bash
python scripts/infra_runtime_recovery_cli.py --instance <root> [--json] \
    [--supply-socket <sock> --subject <subject>]
```

## 已落地（本单）

- **只读分类** `components/life/scripts/runtime_recovery_review.py`：按 instance.json、authority
  marker、WAL schema/`store_revision` 与 journal 行数、AR-0 快照与 open submission fence，
  给出 `ASSEMBLABLE` / `NEEDS_EXISTING_RECOVERY` / `MANUAL` / `READ_BLOCKED` / `UNKNOWN`。
  不取 flock、不调用 `load_state`、不重放、不建目录。
- **WAL 归属**：AG-1 与 AG-2 integration 共用文件名 `commit_intent.json`，schema 不同
  （`chiyo.agency.decision_commit_intent.v1` / `chiyo.agency.life_integration_wal.v1`）。
  不能只看文件存在就拒，也不能绕过它读半快照；按 schema 认领后与各自 journal 行数比较。
  认不出的 schema 报 `UNKNOWN_SCHEMA`，不猜。
- **恢复**：崩溃后由既有 `LifeIntegrationCoordinator.recover_all_and_reconcile(
  auto_reconcile_unknown_actions=True)` 把 open fence 转 UNKNOWN，再按原 operation key
  reconcile；演示中 owner 效果已提交、答复丢失，恢复后版本数不增。
- **writer 冲突**：第二个真实装配被 LR-2 `ActivityAuthorityLease` 拒，业务零写；
  CLI 只据 marker + live pid 报 `MANUAL`，不探测 flock，也不把“进程不在”当已释放。
- **I/O 不持事务锁**：演示在认知调用阻塞期间，只读 CLI、真实 Supply owner 读都可继续；
  对 AG-1 的 `run/.agency_decision_io.lock` 做非阻塞写锁探测，无人持有。
  具名事实：`ActionStore`/`DecisionStore` 的 `_io_lock` 目前只有定义、未被调用；
  AR-0 提交依赖 lease + capability，不另持文件锁。**LR-2 lease 本身在进程生命周期内一直持有**，
  它是 writer fence，不当事务锁。
- **F164 已有 CAS**：旧 `expected_head` 的提交被 Supply owner 拒（CONFLICT），零新版本；
  已 SUCCEEDED 的 Action 再次提交是 `idempotent_replay`，适配器调用 0 次。

## 缺口一：跨 owner 乱序 dependency buffer（F164 后半）

本仓库没有持久 dependency buffer / `PENDING_CAUSE` 状态；现有只有各 owner 自己的 revision
与 AR-0 的 UNKNOWN→reconcile。按原工单要求，**不新造 buffer/时限策略**。要做需要先拍板：

1. 引入一个持久 buffer owner（新表/新文件格式）——超出本单写集；
2. 或者明确“事件先到但 attempt 未提交”一律 `STILL_UNKNOWN`，由人工/下一次操作重试；
3. 或者接受只有每 owner 有序、跨 owner 无全局顺序（当前事实）。

推荐先维持现状并把“缺 buffer”作为已知边界，不在恢复检查里伪造顺序。

## 缺口二：F165/F166 备份恢复与迁移

本仓库**没有**正式备份恢复或迁移入口：没有“进入 RECOVERY_ONLY → 取备份外新 writer epoch →
加载至少同新的删除/撤权高水位 → 结构/语义迁移 → 按原 key 对账”的实现。现有只有
per-store WAL 恢复和实例 prepare。CLI 如实报 `NOT_IMPLEMENTED`。

**正常重启不是备份恢复，恢复备份也不是把副作用回滚。** 要做需要先有：
备份完整性/格式/迁移可行性的只读校验入口、备份之外的 writer epoch/fence 权威、
删除/撤权高水位来源与 `RECOVERY_ONLY` 模式；这些都不能靠复制目录或改 Memory 准入实现。

## 2026-10-29 已接上的入口（含局限）

只读 review 不变；显式恢复现在就在同一个 CLI 上：

```
python3 scripts/infra_runtime_recovery_cli.py --instance <root>            # 只读复核
python3 scripts/infra_runtime_recovery_cli.py --instance <root> --recover  # 显式恢复
```

- 恢复走实例自身的 owner 绑定与正规 lease，调用既有
  `recover_all_and_reconcile(auto_reconcile_unknown_actions=True)` 和 AR-0
  `reconcile_action`；只按原 operation 对账，不 submit、不换 key、不调用模型，
  `finally` 里正规关闭释放。
- 缺绑定、只读预检 MANUAL/READ_BLOCKED、装配被 lease 拒、owner 离线，分别具名
  `BLOCKED` / `STILL_UNKNOWN`，并保持 adapter 提交次数 0；不会把“没查到”当成功。
- 输出只列 action/operation id、confirmed/still_unknown/blocked、结果与后果投递计数、
  Memory disposition 与 adapter 提交次数；不回显对象、正文、prompt、key、env。
- 仍未覆盖：完整 crash 矩阵、备份恢复与迁移（同缺口二）、跨 owner 乱序 buffer
  （同缺口一）、把回执当 Activity 完成或 Memory 已记。真实用法与 demo 证据见
  `.agents/notes/implemented/feature/2026-10-27-infra-runtime-recovery.md` 的
  “后续（2026-10-29）”一节。

## 2026-10-29 后果承接：life 已消费、策略拒绝域各自具名、恢复不多写 M0

显式恢复确认原 operation 之后，同一 CLI 现在把后果按域分开报告：

```
python3 scripts/infra_runtime_recovery_cli.py --instance <root> --recover
```

- **AR-1 四域**：`life`（LR-5）实际消费；`memory` / `goal` / `world` 是生产
  `RefusingSettlementConsumer` 的策略拒绝。`--recover` 逐事件列出每个域的
  `status/disposition/last_error_ref`，并把 life 是否消费了该 settlement、绑定在哪个
  Activity 上单独列出来。
- **outbox pending 不再被误读成“漏投”**：memory/goal/world 拒写会让事件按设计保持
  `pending_outbox_event_ids`，CLI 的 `pending_reason` 明确指向策略拒绝域；它不清零、
  也不把“SETTLED”说成全部后果已完成。
- **AG2 经历交接与 Memory 准入分开报告**：本次恢复没有新增交接时显示
  `dispatched_now=0` 与既有交接数；Memory 本次进程 `accepted=0` 只说明“没有新交接
  可准入”，不代表 AR1 memory 通知成功，也不代表 M0 没有历史记录。M0 的实际记录应由
  Memory 只读产品口核实，不能用 consumer 的 ACCEPT 计数冒充。
- **装配缺口修复**：`RefusingSettlementConsumer` 过去继承 `FakeMemoryConsumer` 的
  硬编码 `consumer_domain="memory"`，导致 goal/world 注册时互相覆盖、只剩 memory 一条
  记录。现在每个拒绝 consumer 用自己校验过的域名注册，四域各自留有具名拒写记录；
  拒写行为不变。
- **只读消费证据**：`ActivityProgressReadService.list_consumed_settlement_refs()` 只读
  LR-5 已消费的 settlement 引用，用于把“life 真的消费了原 action/settlement/同 Activity”
  与 delivery 的 ACCEPT 对上；不改任何进度/完成规则。
- **仍未覆盖**：本次真实用法中 Activity 仍是 ACTIVE，没有产生 `COMPLETED/ABANDONED`
  经历交接，所以“经历交接中途故障、下一次恢复按原身份重投”只在既有测试/演示中有路径，
  本单不造 handoff 凑场景；完整 crash 矩阵、备份恢复与迁移、跨 owner 乱序 buffer 仍同
  缺口一/二。真实用法与检查见 `scripts/life_recovery_consequences_demo.py` 与
  `tests/test_life_recovery_consequences.py`。

## 2026-10-29 补：Action 已成功，但原经历交接待重试

同一入口现在也区分“没有未决 Action”和“没有待投递 handoff”：

```
python3 scripts/infra_runtime_recovery_cli.py --instance <root> --recover
```

- 既有 `recover_all_and_reconcile()` 本来就会在最后重投 `pending_handoff_ids`；本单只是
  让 `recover()` 的状态判定把这次投递算进来，不新增参数、表或 owner。
- 没有 handoff 时状态完全沿用 Action 侧；有 handoff 时：
  **全部交付 → `RECOVERED`**，仍 `deferred`/`FAILED_PENDING_RETRY`/pending → **`PARTIAL`**
  （exit 4），文本输出列出待重试交接 id。Action 已 `SUCCEEDED` 不再自动等于“无事可做”。
- Memory 未配置或写失败时，交接保留原 `lexp:result:` 身份与状态，不造 `ACCEPT`；
  Memory 恢复后再跑同一命令才完成那一次准入。
- 仍未覆盖：完整 crash 矩阵、备份恢复/迁移、跨 owner 乱序 buffer；不保证每个成功都入记忆，
  也不把恢复的 ACCEPT 当 Activity 完成。真实用法见 `scripts/life_recovery_handoff_demo.py`。
