# F-154 忘记接纳、typed 来源权威与受治理擦除（2026-10-28）

## 2026-10-28 收尾返修（首交漏洞保留，不以后绿改写历史）

首交 demo/测试曾把**正整数 `barrier_seq`** 当作擦除凭证：Supply `erase_source` 只检查
`barrier_seq >= 1`，M0 同样只错 actor/seq=0，于是同一 owner 在同一 seq 下可以擦任意
sibling 事件或无关 artifact；typed 来源虽存 `{kind, namespace, ref}`，消费者仍用裸 ref
匹配；派生是 demo 手造 document 并把 artifact id 硬列进 Memory refs，不是真实血缘；
恢复只是可选 utility，构造 owner 即可读旧正文，且正控用了另一份 after 备份；`COMPLETE`
曾在备份仍含正文时只用“受控当前副本”重定义。以上均属首交事实，已由本次返修修正：

- **owner 发放、owner 复核的 `ErasureGrant`** 取代整数：绑定 case/seq/digest/闭包/typed 来源；
  M0 和 Supply 各自重新投影保留权威、核对 owner/conversation、拒绝闭包外目标；
  `EvidenceStore.erase_payload` 无 grant 直接 DENY。
- **typed 三元组精确匹配**：`(kind, namespace, ref)` 全等才扣留；相同 ref 不同
  kind/namespace 是另一来源。
- **真实血缘**：Practice 在 owner 提交时登记 `(memory, event)` 来源，Sharing 登记
  `(artifact, source)`/`(artifactversion, source)`；Supply 用自己的 `artifact_source_edges`
  做有界逆向闭包。Memory 事件不再手列派生 id。
- **恢复激活门**：恢复副本的 owner 读写都校验 `RecoveryGate` marker；`recovery_apply` 扫的是
  同一个恢复副本并在全部 owner ack 后激活。marker 记录 authority head，之后的删除会再次关上。
- **物理/retention 如实**：版本正文、标题（tombstone）、request digest、投影哈希都清；
  备份仍在时 case 保持 `WAITING_RETENTION`，本地 `local_erasure_complete=true` 另报。

范围：本机 operator 的显式忘记请求 → 保留权威先落屏障（事件 + typed 来源）→ 各 owner 自己
失效/擦 payload/清派生 → 恢复后仍扣留且不可再准入。只对隔离合成数据做删除演示，不动真实数据。

## 入口

```bash
python scripts/forget_request_cli.py --instance <phone/Life 实例根> \
    request --operation-key operator:forget-1 \
    --source-ref artifact:... [--event evt:...] \
    [--hold] [--retention-pending] [--uncovered derived_surface]

python scripts/forget_request_cli.py --instance <根> status  --case forget:...
python scripts/forget_request_cli.py --instance <根> retry   --case forget:...
python scripts/forget_request_cli.py --instance <根> confirm --case forget:...
python scripts/forget_request_cli.py --instance <根> list
```

- `--instance` 读该实例自己的 nested Life environment；**不接受调用方自报 owner/operator**。
- 协调库（`NYA02_FORGET_CASE_DB`）与 authority 必须在实例自身备份/恢复集之外；在实例根内、
  符号链接或非本 uid 一律具名拒绝。CLI 的 `--case-db` 必须与实例配置相同。
- 派生 owner 由实例环境显式配置：`NYA02_SUPPLY_ARTIFACT_DB`（文档正文）与
  `NYA02_SUPPLY_PROJECTION_ROOT`（投影文件）。没有配置时，事件指向的 artifact 会作为显式
  uncovered 面，不假装已清理。

## 三段

### 1. typed 来源资格（保留权威）

`authority_records` 新增 `blocked_sources_json` 列，与 `blocked_event_ids` 是**两个标识域**。
每条来源是 `{kind, namespace, ref}`，`ref` 是调用方给的 opaque canonical id（artifact /
经历 id），不是正文、名字或可猜的内容哈希。

- 旧行没有来源时不把 `blocked_sources` 纳入链 payload，因此旧 digest 照旧验证；旧库由
  `ALTER TABLE` 单向补列，不重写历史。
- 读与准入（`ForgetGate` → `LifeExperiencePort.append_outcome` / `recent` / `search` / `read`）
  把 event 自身 `source_refs` 映射成 `(kind, namespace, ref)` 后与 `blocked_sources` **全等**
  匹配；不用子串、名字或正文哈希。相同 ref 但不同 kind/namespace 是另一个来源，仍然可见。
  record 只有 primary ref 时，只在该 ref 属于 memory 域时匹配，不去猜测 supply artifact。
- 来源写在 authority 里，**不依赖 case 是否仍 HELD**：`confirm` 后 case 进入完成态，同来源换
  event id 重投仍在写入前被拒。
- `hold` 仍是 case 级保留（立即扣留、不写 DELETE、跨重开）；`confirm` 才把 scope 转成删除。
- authority 缺失 / 链不一致 / 低于 floor / currentness 不可证 → 读与新删除都具名阻断；
  不是“空记忆”。

### 2. M0 受治理物理擦除

`MemoryControlLedger.erase(operation_id, event_ids, actor, grant)` 是唯一的 M0 擦除口：

- **owner 自己校验**：actor 必须是本 owner；`grant` 必须由本 owner 的
  `MemoryDeletionAuthority.authorize(case_id, ...)` 发放，并在写入前用
  `verify_grant` 重新投影核对 case/seq/digest/闭包与当前权威。正整数 `barrier_seq`
  不是凭证，没有 `operator=true` 捷径，也没有默认屏障；同一 owner/seq 擦闭包外的事件被拒。
- 作用域重新对着 evidence 校验（owner + conversation），不能擦别人的会话。
- 幂等：同一 `operation_id` 重放；不同 scope 同 key 是 collision。
- `EvidenceStore.erase_payload` 在单个事务内抬起 append-only 触发器，把 `content` 与
  `source_refs_json` 清空、写 `payload_erased=1`、保留 event 身份/turn/审计，然后
  `secure_delete=ON` + WAL checkpoint + `VACUUM`。读路径把 `payload_erased` 行当作不可召回。
- 已完成的外部 Action / receipt 不倒写；只清 M0 正文与其索引。

### 3. 真实派生清理与恢复

派生面清单（本次真实存在 / NOT_PRESENT）：

| 面 | 状态 |
| --- | --- |
| M0 `evidence_events.content` + `source_refs_json` | 已由第 2 段清 |
| Supply `artifact_versions.content`（Practice/Sharing 文档正文唯一副本） | 已清 |
| Supply 投影文件（`artifact_projection_bindings`） | 已覆盖为空 |
| embedding / episode / FTS 索引 | NOT_PRESENT（当前无实现） |
| continuity / reflection 缓存副本 | NOT_PRESENT（读取时重算，无独立持久副本） |

`ManagedArtifactStore.erase_source(operation_key, authorized_subject_id, grant, authority,
artifact_ids, version_ids)`：

- 需要 owner 发放的 `grant` **和**可信 authority 实例；Supply 自己再投影核对，并只接受
  `source_closure(grant.sources)` 里的 target。闭包外的同 subject artifact（哪怕同一 seq）
  一律 DENY。
- `artifact_source_edges` 记录真实血缘：`create_artifact` / `commit_markdown` 在
  `source_refs` 非空时同事务登记；逆向闭包有深度上限与环路 visited 集。
- 擦除每个命名 artifact 的**所有版本**正文，写 `content_erased=1`，同时把 `content_sha256`
  更新为空正文哈希（不把旧正文指纹留在同一行）；版本身份/编号/operation 保留。
- 每个绑定的投影文件被覆盖为空表示；失败会作为 `projection.state=FAILED` 上报，不静默。
- 幂等 under operation key；Supply 主库同样 `secure_delete` + checkpoint + `VACUUM`。
- Practice/Sharing 没有独立持久库：它们的正文就是这些版本，擦除后各自解析器读到空文本，
  不会再被当作有效空文档。

`recovery_apply(authority_db, owner, conversation, recovered_evidence, supply_store,
recovery_gate)`：对每条已提交决定重新 `authorize` 一个 grant，对**同一个**恢复副本重施
event 擦除与 Supply 逆向闭包清理；全部 owner ack 后由 `RecoveryGate.activate` 写入
marker（owner/conversation/authority seq+digest）。恢复副本的
`ManagedArtifactStore`/`LifeExperiencePort` 在 marker 未 OPEN 或 authority head 已前进时
对读与新写抛 `RecoveryBlocked`。权威缺失/陈旧/不可证 → `BLOCKED`，绝不返回 READY，也不
初始化新 floor 掩盖旧代际。旧 self-history epoch 抑制旧自述是另一层：它不等于删除，
恢复正控使用激活后新写的事件，不为此重写 epoch 策略。

### 4. 生产接缝（端口、服务、操作侧与恢复 launcher）

- `LifeArtifactPort.create_artifact/commit_version` 接受 `source_refs`（`{kind, ref}` +
  可选 `namespace`/`generation`），并放进同一 owner 命令的嵌套 `artifact` 字段。坏形状在
  端口本地就拒绝，不会悄悄写出一份没有血缘的文档。
- `LifeSupplyService` 的 `create_artifact`/`commit_markdown` 嵌套白名单只新增
  `source_refs`；形状不合法 → `DENY/FIELD_TYPE:source_refs`；非空时同时进入授权 payload，
  并把 refs 交给 store 在同一事务登记边。空 refs 的旧请求字节不变。
- 恢复 gate 由**正式 launcher** 装配：`scripts/supply_service.py` 发现
  `NYA02_F154_RECOVERY_DIR`/`CHIYO_F154_RECOVERY_DIR` 时，用实例的
  `NYA02_MEMORY_SUBJECT` + `NYA02_MEMORY_AUTHORITY_DB`（可选 `MIN_SEQ`）构建
  `RecoveryGate` 并交给 `ServiceSettings.recovery_gate`；缺 owner/authority 时进程拒绝启动
  （`RECOVERY_GATE_CONFIG_INCOMPLETE`），不会以“无 gate”的方式开放恢复副本。未激活时
  socket 回答 `BLOCKED/RECOVERY_NOT_RUN`（读写都是确定拒绝，不是 UNKNOWN）。
- **NO_CHANGE 携带 `source_refs` 具名拒绝**：内容与 head 相同时，带 refs 的提交回答
  `BLOCKED/SOURCE_REFS_REQUIRE_NEW_VERSION`（早于点 owner 提交、早于任何边写入），不再用
  “已经是这个正文”糊掉未登记的血缘；不带 refs 的旧重放仍答只读 `NO_CHANGE`，授权复查保持。
  正式 `LifeArtifactPort → LifeSupplyService` socket 负控见
  `tests/test_f154_source_lineage_socket.py::test_identical_content_with_source_refs_is_refused_by_name`
  与 demo 的已删来源用例：版本行与边零增，head 不动。
- 操作侧（`forget_request_cli.py` 的 request/retry/recover）打开服务 canonical 文件时传
  `fences_root`：它取得同一个 `ManagedArtifact` writer fence；活服务持有时 `FenceHeld`，
  停服后取得并持久激活 epoch。store 在单库布局下不再安装旧 0-arg 守卫，并清理早先遗留的
  `c6_fence_*` 触发器，否则活服务的下一次写会因缺 SQL 函数而失败。
- **`--recovered-controls` 现在真的被读取、校验并绑定**（`recovered_controls_identity`）：
  `mode=ro` 打开，不做建库/迁移；核 `PRAGMA quick_check`、`memory_controls` 表与列、
  每行 owner/conversation、以及（有行时）每个 `target_event` 必须存在于**该恢复副本**的
  evidence 里。缺失 → `RECOVERED_CONTROLS_MISSING`；非法库 → `CORRUPT`/`UNREADABLE`/
  `BAD_SCHEMA`；别的 owner → `OWNER_MISMATCH`；不在显式恢复目录内或指向活实例
  canonical/authority/case → `OUTSIDE_COPY`/`LIVE_PATH`；符号链接 → `SYMLINK`；悬空引用 →
  `DANGLING`。全部发生在任何擦除、fence 或 marker 写入之前。校验结果写进 marker
  （`recovered_controls` / `_digest` / `_identity`，同时绑定 `recovered_supply` /
  `recovered_projection`）。正式 Memory 服务与 Supply owner 在打开前 `verify_copy`，
  文件与 marker 不符 → `RECOVERY_COPY_MISMATCH`；不再只靠 demo 手传 gate。
- **激活必须绑定 controls，没有省略入口**：正式 CLI 不传 `--recovered-controls` 时，在扫描
  / fence / marker 之前具名拒绝 `RECOVERED_CONTROLS_REQUIRED`（exit 2）；`recovery_apply`
  在带 `recovery_gate` 时、以及 `RecoveryGate.activate` 写 marker 前，同样拒绝“无绑定的
  OPEN marker”。修前实测：旧 CLI 省略该参数会返回 `RECOVERED_OPEN` 并写出 marker，正式
  Memory 服务指向该副本仍 READY、可读可写——这是本次收掉的入口，不是历史猜测。
- **非空 controls 正控已跑通**：恢复副本里用正规 `MemoryControlLedger.apply` 写一条
  `DELETE` 控制行（不是 SQL 伪造），正式 CLI 真读并绑定 `identity=VERIFIED`/`rows=1`，
  marker 同时存路径与 sha256；激活后原控制效果仍遵守（被控事件不可读），控制行之后写下的
  无关内容可读，正常新写可用。`verify_copy` 只比对**路径**（digest 写进 marker 供记录，
  打开时不做逐字节比对），因此同一
  副本上的正常治理写、Memory 新经历不会变成虚假 `RECOVERY_COPY_MISMATCH`；保留权威前进仍按
  既有 freshness 规则要求再扫（`RECOVERY_AUTHORITY_ADVANCED`），不把普通写当副本替换。
  保留：空的 controls 副本只能证明“没有控制行”，不能证明身份（marker 记为 `EMPTY`）；
  合法旧 controls 不重写；不新增外部权威，`authority+floor` 同时回滚仍不可信。

- **激活绑定代际，不只是路径（2026-10-28 场景 9）**：marker 原来只记路径，停服后把删除前的
  evidence/controls/Supply 回填到**同一路径**，路径全等、authority 未动，副本会带着旧正文重新
  可读。现在三个库各自在表 `recovery_incarnation`（按 `kind`=evidence/controls/supply 一行）持久化
  `generation/owner/conversation/authority_seq/authority_digest`，由各自 owner 的
  `stamp_recovery_incarnation()` 在擦除后、写 marker 前落盘；`activate` 没有代际就拒绝
  （`RECOVERY_INCARNATION_REQUIRED`），marker 记 `incarnations`。`verify_copy` 每次按 kind 重读
  库里的代际：旧备份缺表/缺行 → `RECOVERY_INCARNATION_MISSING`，代际不同 →
  `RECOVERY_INCARNATION_MISMATCH`；没有 `incarnations` 的旧 marker → `RECOVERY_INCARNATION_UNBOUND`
  （fail closed，必须重跑正式 `recover`）。`LifeExperiencePort` 的每次读写先 `verify_copy` 再
  `ensure`，Supply 的 `_require_open` 同理，因此在打开任何正文之前就拒绝；普通业务写不改代际，
  正常重开不误报。取舍与未覆盖见
  `[代际绑定笔记](../../.agents/notes/implemented/bug-fix/2026-10-28-recovery-incarnation-binding.md)`。
  只承诺“正规停服后再恢复”；已开连接上的并发文件替换不支持。投影文件不是数据库、没有代际，
  由下一小节的恢复期重建处理。
  验收补齐（同一恢复用法）：三个库**各自**和**全部**回填旧备份后，Memory 的读/新写与 Supply 的
  正式读/有合法 grant 的新写都按代际拒绝（Supply 零版本/零边）；三库全回填后重跑正式 `recover`
  换发新代际，被删 M0 与作品版本正文不可读（版本行仍在、content 空）、无关作品正文保留；
  激活后正常业务写与正常治理写可继续，同源换 event id 重投仍拒且 M0 零增；主 case 由 request 的
  `--retention-pending` 登记删除前备份并停在 `WAITING_RETENTION`，`local_erasure_complete` 是
  对当前受控存储的独立布尔读，不把“备份还在”当成 `COMPLETE`。

### 5. 恢复期的投影重建与消费门（2026-10-28 场景 9 投影回填）

Markdown 投影是表示，不是第二份权威；它是文件，没有数据库代际。停服后把删除前的投影文件
放回**同一路径**，路径与三个库的代际都相符，文件本身却带着旧正文。本段把这一面补上：

- **恢复期从 canonical 重建已登记投影**：正式 `recover` 在擦除之后、stamp/marker 之前调用
  `ManagedArtifactStore.reconcile_registered_projections()`。它只遍历 owner 自己
  `artifact_projection_bindings` 里登记的投影，按当前 canonical head 重写（被删版本正文为空，
  文件即空表示）；未登记文件不猜、不删、不改名；字节已与 head 相同的文件不重写，正常作品
  更新与投影重建不受影响。符号链接、逃逸路径、非普通文件、缺 head 等进入具名 `blocked`，
  任一失败 → `BLOCKED`，**不写 marker**。
- **消费前先核 canonical**：显式 `import_projection` 在读取任何文件字节之前，先问 owner
  当前 head 是否已被治理擦除（`content_erased=1`）。是 → `BLOCKED/CANONICAL_PAYLOAD_ERASED`，
  零新版本；因此“重启但未重跑 recover”的旧文件也不能把正文导入回来。`observe_projection`
  仍只报 `DIVERGED`/哈希/行数，答案不带正文；`rebuild_projection` 对 `DIVERGED` 继续拒绝。
- **不把读取扣留当文件已清**：重启只证明产品入口不回读；文件字节的真实清空以恢复期重建后的
  磁盘读为准（demo 用合成 sentinel 实测为空，不以 marker 标志代替）。
- 边界：只承诺“正规停服 → 正式 recover → 正式入口重开”。在线替换、绕过 owner 直接读文件、
  管理员篡改所有元数据/authority+floor 整机回滚不在承诺内；不扫描目录区以外的未登记文件，
  也不为每个文件建 hash 注册中心。

## 状态

`RECEIVED` → `HELD`（范围不清 / 目标未找到 / 显式 hold）或 `BARRIER_APPLIED` → `OWNER_PENDING`
→ `WAITING_RETENTION` / `COMPLETE`；权威不可证时 `BLOCKED`。

- `COMPLETE` 由 owner ack + 布尔物理读共同决定：M0 正文已擦、已配置的派生 owner 已 ack、
  没有被显式列为未覆盖的面。缺省 flag 不再能把仍带正文的 case 推成 `COMPLETE`。
- `WAITING_RETENTION` 的具名原因：离线备份未擦、显式未覆盖派生面、物理正文残留。
- `OWNER_PENDING`：某个 owner 应用失败；已生效的屏障与已擦的正文不回滚，`retry` 用同一
  operation identity 补齐，不新增删除 generation。
- `HELD` 持久、不自动超时；`retry` 不升级 hold，只有 `confirm` 能转删除。

## 可信前提与保留

- authority 与 floor 同域；同机换目录不等于独立主机。两者一起被回滚时本机不能自证最新，
  需要外部保管的 floor 交接。
- 备份副本（演示里保留的删除前 evidence/controls/authority/Supply）**仍含正文**：这属于
  retention 决定，所以主 case 停在 `WAITING_RETENTION`；“受控当前存储与已接派生面无正文”
  另报 `local_erasure_complete=true`。**不把 `COMPLETE` 重定义成“当前副本已擦”**：只有保留
  副本本身也被处理，case 才可能 `COMPLETE`。
- 物理擦除指当前受控存储可恢复 payload 与其 SQLite 索引/日志副本；不承诺 SSD 取证或此前被
  管理员复制的副本。

## 未覆盖（具名）

- **旧无血缘派生**：逆向闭包只沿 owner 自己登记的 `artifact_source_edges` 走。旧实例里
  先于本机制产生、没有 edge 的派生不会被自动发现；这是一处具名阻点，不能当作“无依赖”。
  显式 `--uncovered` 只让 case 如实停在 `WAITING_RETENTION` 并点名该面，**不是**“已找到/已清理
  旧派生”的保证，也不提供自动补血缘。
- 投影面：只在正式 `recover` 中重建**已登记**绑定；目录里未登记的文件、在线运行时的文件替换、
  绕过 owner 的直接文件读取，都不在承诺内（见第 5 小节边界）。
- 换伪 ref / 故意剥掉来源引用的规避不在识别承诺内。
- 跨主机 / 整机回滚 / 多人多设备并发。
- 已发生的外部投递不伪撤回；成功 Action 不倒写 `NOT_EXECUTED`。

## 验收

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/forget_generation_demo.py'
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_f154_source_lineage_socket'
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_forget_generation_barrier tests.test_f154_typed_source_erasure'
```

演示根保留在 `.agents/worker-runs/f154-complete-demo-<utc>-<pid>`（或 `FORGET_DEMO_ROOT`），
不删除、不 unlink、不擦除备份。
