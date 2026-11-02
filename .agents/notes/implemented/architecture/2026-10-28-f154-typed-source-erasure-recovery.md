# Agent Note: F-154 三段落地 —— typed 来源权威、M0 擦除、派生清理与恢复

Status: implemented（限定范围，见 Consequences）。前置历史见
[F-153/F-154 事件屏障](../feature/2026-10-28-forget-generation-barrier.md)；恢复权威的开放问题见
[memory restore control authority](../../proposed/architecture/2026-10-26-memory-restore-control-authority.md)。

## Problem

事件级屏障只能覆盖已知 event/turn：换 event id 重投同来源仍可写回；M0 append-only 只有逻辑扣留，
没有正式物理擦除口；Practice/Sharing 的正文副本落在 Supply artifact 版本里，删除后仍可读。
没有一条恢复路径能在开放读取前把旧备份里的正文重清一遍。2026-10-28 用户本人直接批准扩展删除
权威并完成这三段，允许必要的旧 owner 合同与持久格式由监工设计。

首交（同日）未达本 note 下面这套边界，已在收尾返修中修正，历史保留：首交把正整数
`barrier_seq` 当凭证（Supply 只查 `>=1`，M0 只错 actor/seq=0），同一 owner 同 seq 可擦
sibling/无关 artifact；typed 三元组存了却按裸 ref 消费；派生是 demo 手造 artifact 并
硬列进 Memory refs，不是 owner 血缘；恢复是可选 utility，构造 owner 即可读正文，正控还用了
另一份 after 备份；`COMPLETE` 曾在备份仍有正文时被“受控当前副本”重定义。

## Decision

1. `MemoryDeletionAuthority.authority_records` 增加 `blocked_sources_json`：typed 来源
   `{kind, namespace, ref}`，与 event-id 域分开；旧行不纳入该键，链 digest 与旧读法不变。
   `ForgetGate` 与 `LifeExperiencePort` 把 event 自身 `source_refs` 映射成
   `(kind, namespace, ref)` 后**全等**匹配；相同 ref 不同 kind/namespace 是另一来源，仍可见。
   删除后 case 离开 HELD 也不会让来源重新可准入。
2. M0 的正式擦除口是 `MemoryControlLedger.erase(...)`：它只接受 owner 的
   `MemoryDeletionAuthority.authorize(case_id, ...)` 发放的 `ErasureGrant`，并再次
   `verify_grant` 核对 case/seq/digest/闭包；正整数不是凭证，闭包外 sibling 被拒。作用域对
   evidence 复核，按 operation 幂等；`EvidenceStore.erase_payload` 无 grant 直接 DENY，在单事务
   内抬 append-only 触发器清正文/来源 ref、写 `payload_erased=1` 与不可猜 tombstone，再
   `secure_delete + checkpoint + VACUUM`。已完成的外部回执不倒写。
3. 真实派生 owner 是 `ManagedArtifactStore`：`erase_source(...)` 要求 owner grant **与**可信
   authority 实例，Supply 自己再投影核对，并用 `artifact_source_edges` 做有界逆向闭包；闭包外
   的同 subject artifact 一律 DENY。`create_artifact`/`commit_markdown` 在 owner 提交时同事务
   登记来源边；Practice 登记 `(memory, experience)`，Sharing 登记源 artifact/version。
   `erase_source` 清所有版本正文（`content_erased=1`、正文哈希改空）、标题改不可猜 tombstone、
   request digest/投影哈希改空，覆盖投影文件，幂等并 `VACUUM`。
   Practice/Sharing 没有独立持久库，正文唯一副本就在这些版本里。
4. `recovery_apply(...)` 对恢复副本的每条已提交决定重新发放 grant，扫**同一个**恢复副本的
   event 与 Supply 逆向闭包，全部 owner ack 后写 `RecoveryGate` 激活 marker（owner/
   conversation/authority seq+digest）。恢复副本的 store/port 在 marker 未 OPEN 或 authority
   head 前进时对读与新写抛 `RecoveryBlocked`；权威缺失或低于 floor 直接 `BLOCKED`，不新建
   floor 掩盖旧代际。旧 self-history epoch 抑制是另一层，不当删除；正控用激活后新写事件。

## Alternatives considered

* **只做派生清理，不扩来源权威**。最强理由是改动最小、先把“忘了但派生物还在”补上。否决原因：
  换 event id 重投同来源仍能写回，删除不成立；三段缺一不能称 F154 完成。
* **把 typed source 塞进 `blocked_event_ids`**。最强理由是零 schema 改动。否决原因：字段与全部
  既有消费者都是 event-id 语义，会产生只有新 gate 认识的第二域，且修不好 freshness。
* **物理删除整行（DELETE）而非清正文**。最强理由是存储最干净。否决原因：会丢 event 身份/turn/
  审计，破坏既有多处按 event id 的对账与幂等；改为保留身份、清正文并标记。
* **用独立小表/第二库存 source blocks**。最强理由是隔离清晰。否决原因：会绕开同一个 floor 与
  回滚问题，正是先前 V-C 恢复失败的形态。

## Consequences

* 收益：同源换 id 重投写前拒；M0 正文真正从受控存储消失且 byte 检查通过；Practice/Sharing 的
  版本正文与投影都被清；恢复副本先清洗后开放读取；一处 owner 失败时屏障保留、可幂等 retry。
* 代价与边界：删除前的备份副本仍含正文，所以 case 停在 `WAITING_RETENTION`，**不得**把
  `COMPLETE` 重定义成“当前副本已擦”；“当前受控存储与已接派生面无正文”是另一个字段
  `local_erasure_complete`。只有保留副本本身也被处理，case 才可能 `COMPLETE`。retention
  必须如实报告；authority 与 floor 同域，整机回滚不在承诺内。embedding/
  episode/FTS 与 continuity/reflection 缓存当前无独立持久实现，标 NOT_PRESENT，不建空表冒充。
* 未覆盖：换伪 ref 规避、跨主机/整机回滚、多人并发、已外发投递的撤回；恢复正控没有绕过既有
   self-history epoch，旧自述仍可能被 epoch 抑制（不等于删除）。只对隔离合成数据验证，未碰
  真实数据/旧实例，未调用产品模型。

## 生产接缝收尾（2026-10-28 晚，同功能最后一单）

**Problem**：首交的演示用自制 `_StorePort` 直接调 `ManagedArtifactStore`，`source_refs` 只存在于
本进程对象上。正式链三处断开：`LifeArtifactPort.create_artifact/commit_version` 不接受该参数，
`private_project_service` 的 `TypeError` 回退会**静默丢掉**血缘；socket 服务端嵌套字段白名单没有
`source_refs`；恢复 gate 只在 demo 与 CLI 里手构 `ManagedArtifactStore(recovery_gate=...)`，
正式 `LifeSupplyService` 既不读 `NYA02_F154_RECOVERY_DIR` 也会把恢复副本当普通库开放。操作侧 CLI
直接打开服务 canonical 文件还会装回旧 0-arg 守卫、并重置 epoch 行，把活服务写坏。

**Decision**：端口→服务→store 全链携带 typed `source_refs`，服务端严格校验（坏形状直接
`FIELD_TYPE:source_refs`，不静默丢），非空时同时进入授权 payload，store 在同一 owner 提交里登记
边；`ServiceSettings.recovery_gate` 由 `scripts/supply_service.py` 从实例环境构建（gate 模块留在
Life，Supply 只收鸭子类型对象），配置了恢复目录却缺 owner/authority 时拒绝启动；store 在单库布局下
跳过/清理旧 0-arg 守卫、注册 owner 参数函数并持久激活 epoch，新增可选 `fences_root` 让操作侧取
同一把 writer fence（CLI 的 request/retry/recover 都传）；未激活的恢复副本读写都返回具名 `BLOCKED`。

**Alternatives considered**：保留自制 port（最快、演示照样绿）——否决：证明的不是生产链；让操作侧
无锁直写——否决：会与活服务并发写且 epoch 不一致，正是 fencing 要防的；在 Supply 内导入 Life 的
gate 模块——否决：破坏组件层次，改由 launcher 装配。

**Consequences**：固定 demo 现在跑真实 service socket + provisioned workspace/grants + 正式
launcher 的恢复 gate，`ALL_EXPECTATIONS_MET`；恢复未激活读/写均 `BLOCKED/RECOVERY_NOT_RUN`，
激活后同一 launcher 读到已擦版本为空、无关作品保留；操作侧写要么拿到 fence，要么 `FenceHeld`。
未覆盖：真实旧实例、同 uid 管理员手工改库、跨机/整机回滚；不称 S14 全过或 G1。

## 恢复后的三个缺口（2026-10-28 夜，同功能收尾）

**Problem**：`cmd_recover` 声明了 `--recovered-controls` 却从不引用它，marker 照样写成 `OPEN`
——用合成根实测：同一副本里放一份 **别的 owner** 的 controls，不传该参数时 sweep 仍返回
`RECOVERED_OPEN` 并写出 marker（这份账本从未被打开）。marker 也只绑定 evidence，服务侧没有
“你打开的是被激活的那一份吗”的检查。

**Decision**：`recovered_controls_identity` 以 `mode=ro` 真读 controls（不建库/不迁移），核
`quick_check`、表与列、每行 owner/conversation、有行时每个 target_event 必须存在于该恢复副本
evidence；缺失/坏库/错身份/符号链接/目录外/活实例自身/悬空引用各自具名拒绝，全部先于擦除、
fence 与 marker 写入。校验结果（路径+sha256+identity）与 `recovered_supply`/`recovered_projection`
一起进 marker；`RecoveryGate.verify_copy` 由正式 Memory 服务与 Supply owner 在打开前调用，
不符 → `RECOVERY_COPY_MISMATCH`（空副本记 `EMPTY`，只证“没有控制行”，不证身份）。

**Alternatives considered**：只让 demo 手传 gate 并断言 —— 否决：生产入口仍会开放任何同名文件；
把 controls 行重写进恢复副本以便“验证” —— 否决：校验变成写入，且旧 controls 本可合法保留；
用实例 DB 构造器打开（会自动建表/迁移）—— 否决：那不是只读校验，会把坏副本修成能过。

**Consequences**：demo 新增 6 条 controls 负控 + marker 绑定 + Memory/Supply 不符拒绝，全部
`PASS`，整体 `ALL_EXPECTATIONS_MET`/exit 0；`tests/test_f154_final_gaps.py` 6 例绿；
`test_f154_source_lineage_socket` 新增的 NO_CHANGE+refs 负控绿（现状本就拒绝，属**补证当前已修**，
不是修前红）。未覆盖：跨备份防复活、外部权威；`test_subject_reading_practice_cycle` 中
`test_the_work_writer_no_change_stops_before_the_owner_on_the_ar0_path` **仍红且归因 UNKNOWN**
——安全诊断显示该轮 `outcome=ALREADY_DONE`、`step_path=AR0_READING_CLOSEOUT_WORLD_ROUTED`、
`artifact_selection.selected_by=BOUND_WORK` 指向的 artifact 与夹具传入的 work id 不同，
writer 因此一次没被问；这不像“port 参数兼容漏接”，未改 AG1/cycle 策略，按停止条件具名留红。

## 非空 controls 收尾：缺绑定 fail-closed、VERIFIED 正控（2026-10-28 夜）

**Problem**：上一轮的 controls 正控用的是删除前**空**的 controls 副本（`identity=EMPTY`），
只证明“没有控制行”。同时正式 CLI 的 `--recovered-controls` 仍是可省略参数：同一次收尾实测，
不传它时 CLI 返回 `RECOVERED_OPEN` 并写出 marker，正式 Memory 服务指向该副本仍 READY、可读可写
——即“只凭 evidence 激活”的正式入口还在。

**Decision**：`cmd_recover` 在扫描/fence/marker 之前具名拒绝缺参
（`RECOVERED_CONTROLS_REQUIRED`，exit 2）；`recovery_apply`（带 gate 时）与
`RecoveryGate.activate` 同样拒绝写出无 controls 绑定的 OPEN marker，绑定的不变量落在写 marker
的位置而不只落在 CLI。新开一个合成恢复根，用正规 `MemoryControlLedger.apply` 写真实 `DELETE`
控制行（无 SQL 伪造），正式 CLI 真读绑定 `VERIFIED/rows=1`、marker 记路径+sha256；激活后控制
仍生效、控制行之后写的无关内容可读、新写可用。`RecoveryGate.verify_copy` 保持只比路径：同一
副本上的正常治理写（digest 变）不得被当成错副本；保留权威前进仍按既有规则 `RECOVERY_AUTHORITY_ADVANCED`
要求再扫。

**Alternatives considered**：只在 demo 里传一个非空文件、继续让 CLI 可省略——否决：正式入口
仍会只凭 evidence 激活；把 digest 也纳入每次打开比对——否决：正常治理写会让整个恢复实例被虚假
copy mismatch 封死；拒绝“authority 前进后继续用旧 marker”——否决：那就是既有 freshness 要防的
旧代际重启。

**Consequences**：固定 demo 增“省略 controls 被拒”与 4 条非空 controls 正控，全部 `PASS`、
`ALL_EXPECTATIONS_MET`/exit 0；`tests/test_f154_final_gaps.py` 增至 8 例绿（新增缺参拒绝、
非空 VERIFIED 读取绑定/控制生效/正常治理写不误报）。未覆盖：真实旧实例、跨备份防复活、外部
权威；authority 前进后的真实关闭只按既有规则引用，未在本轮触发；`readingpractice` 那一红保持
UNKNOWN，不在本单改动。
