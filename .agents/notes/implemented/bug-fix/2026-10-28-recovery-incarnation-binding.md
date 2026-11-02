# Agent Note: 恢复激活绑定代际，不再只绑路径（F-154 场景 9）

Status: implemented（限定范围；投影文件改为恢复期从 canonical 重建，在线替换与整机回滚未覆盖，见 Consequences）

**Problem**：`RecoveryGate` 的激活 marker 只记 `recovered_evidence`/`recovered_controls`/
`recovered_supply` 的**路径**，`verify_copy` 也只比路径，`ensure` 只比 authority 的 seq/digest。
用户的实际恢复用法是：停服 → 把删除前的 evidence/controls/Supply 数据库**回填到完全相同
的恢复路径** → 重新起服务。路径全等、authority 未回滚，marker 仍是 OPEN，于是被忘记的正文
在恢复副本里重新可读。这条路径在首交与 10-28 收尾两轮都没被覆盖，属于真实可复现缺口。

**Decision**：由**每个 owner 在自己的库里**持久化一份恢复代际（`recovery_incarnation` 表，
单行按 `kind` 区分：`evidence`/`controls`/`supply`），字段 `generation/owner/conversation/
authority_seq/authority_digest/stamped_at`。正式 `recover` 在擦除完成后、写 marker 之前，
调用 `EvidenceStore` / `MemoryControlLedger` / `ManagedArtifactStore` 各自的
`stamp_recovery_incarnation()` 落一个新代际；`RecoveryGate.activate` 拒绝没有代际的激活
（`RECOVERY_INCARNATION_REQUIRED`），并把三个代际写进 marker 的 `incarnations`。
`verify_copy` 在读路径时按 kind 重新读库里的代际：缺表/缺行 → `RECOVERY_INCARNATION_MISSING`，
代际不同 → `RECOVERY_INCARNATION_MISMATCH`，owner 不符 → `RECOVERY_INCARNATION_OWNER_MISMATCH`；
marker 是旧的、没有 `incarnations` → `RECOVERY_INCARNATION_UNBOUND`（fail closed，必须重跑正式
recover，不静默升级）。`LifeExperiencePort._recovery_check` 每次读写都先 `verify_copy` 再
`ensure`，Supply 的 `_require_open` 本来就每次 `verify_copy`，因此读和新写都在打开任何正文之前
被拦。普通业务写和治理写不动这张表，所以正常重开不会误报。

**Alternatives considered**：(A) 每次恢复激活都按当前 authority 全量扫一遍正文 hash 与旧备份比对。
它最直接，不需要任何 schema 扩展；但每次恢复激活都要全库扫描，而且正常业务写会让 hash 变化，
只能靠“与备份比”来区分，备份一旦被替换就失去参照，反而回到同一个问题，故否决。(C) 只依赖
路径/mtime/大小/inode。`cp -p` 保留 mtime、同尺寸回填都能绕过，正是本次要封的用法，否决。
(D) 只在 demo 里手构 gate 打印代际。读取方不实际消费就等于没修，否决。控件的代际写在本轮
新隔离库的表扩展里，不直接 SQL 擦正文；低层写由各自 owner 的正式方法负责。

**Consequences**：同路径回填旧 evidence / 旧 controls / 旧 Supply 三者各自被具名拒绝，不会
被另外两个库代际正常而掩盖；缺代际是 `RECOVERY_INCARNATION_MISSING`，已有但代际不同是
`RECOVERY_INCARNATION_MISMATCH`（两种都有定点测试）。Supply 的**读**与**有合法 grant 的新写**
都按代际拒绝且零版本/零边；Memory 被阻断时读与新写共用同一个 port，没有写通道。把三个库
**全部**回填旧备份后重跑正式 `recover`，会重新擦并换发新代际：被删 M0 不可读、被删作品版本
仍在但正文为空，无关作品正文保留；激活后正常业务写、正常治理写都可继续，同一已删来源换
event id 重投仍在写入前被拒且 M0 零增。主 case 用正规 request 的 `--retention-pending` 登记
删除前备份，停在 `WAITING_RETENTION`；`local_erasure_complete` 是对当前受控存储的独立布尔
读，不是 case 状态，不把“备份还在”吞成 `COMPLETE`。代价与保留：旧 marker（无
`incarnations`）一律 fail closed，需要重跑一次正式 `recover` 才能用；代际只防“把旧文件放回
同一路径”，不防整机回滚时 authority 与 floor 一起被回滚（那是既有的外部 floor 交接问题）；
投影目录是文件不是库，本版仍只按路径绑定；**已开连接**的并发文件替换不被支持——代际校验
是每次操作按路径重新打开数据库读代际，已持有的 sqlite 句柄仍指向旧 inode，因此只承诺
“正规停服后再恢复”这个用法。这些限定不构成完整场景 9，更不代表全 F-154 通过。原架构 note
的路径绑定历史保留，不改写成已通过。

## 2026-10-28 场景 9 末段增量：投影文件没有代际，恢复期从 canonical 重建

**Problem**：上一版把 evidence/controls/supply 三个库绑到代际，但 Markdown 投影是**文件**，
没有库、没有代际。停服后把删除前的投影文件放回同一路径，三个库各自代际仍相符、marker 仍
OPEN，文件里却是被忘记的正文；正式 `import_projection` 是真实的产品消费口（确认后把文件内容
提交成新版本），重启后它可以把旧正文带回 canonical。首版与代际版都没覆盖这一面。

**Decision**：不在投影上另造“每文件 hash 注册中心”，而是沿已有的 canonical→projection 规则补两处：

1. `ManagedArtifactStore.reconcile_registered_projections()`：正式 `recover` 在擦除后、
   stamp/marker 前，遍历 owner 自己 `artifact_projection_bindings` 登记的投影，按当前
   canonical head 重写（擦除后为空表示）。未登记文件不猜不删；不安全路径（符号链接/逃逸/
   非普通文件）与缺 head 进入具名 `blocked`，任一失败则 `BLOCKED` 且不写 marker。字节已与
   head 相同的不重写，正常提交/重建不受影响。
2. `import_projection` 在读取任何文件字节之前先核 `content_erased`：head 已被治理擦除 →
   `BLOCKED/CANONICAL_PAYLOAD_ERASED`，零新版本。这样“只重启、没重跑 recover”的旧文件也
   不会在消费口复活正文；`observe_projection` 仍只报状态/哈希，不带正文。

**Alternatives considered**：(A) 为投影目录建持久 hash 清单/每文件注册中心，恢复时逐文件比对。
它能统一覆盖未登记文件，但等于新造第二套权威与迁移成本，还会把“目录里多一个文件”变成恢复
失败条件；任务明确要求沿既有一致性规则，否决。(C) 只靠 import 门不重建文件。产品入口确实不
回流，但文件原文还在，“本地派生已清”就成了空话，也违背“不把读取扣留当擦净”，否决。
(D) 只靠恢复期重写、不给 import 门。重启但未重跑 recover 的窗口里旧文件仍可被确认导入，
否决。

**Consequences**：投影回填在正式恢复后**实际被从 canonical 重写**（demo 用合成 sentinel 实测
字节为空）；重启但未恢复时 canonical 读为空、observe 不带正文、import 具名拒绝；无关作品的
正文/投影保留，正常新版本仍提交并更新投影；已删来源换 event id 重投仍拒。边界：只承诺正规
停服→正式 recover→正式入口重开；在线替换、直接读文件、管理员同时回滚 authority+floor、
目录中未登记文件不在承诺内；重建只按 binding 记录走，不做全目录扫描或全库每读校验。
