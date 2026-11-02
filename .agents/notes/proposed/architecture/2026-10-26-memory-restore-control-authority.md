# Agent Note: Memory 恢复的回滚域外控制权威（proposed）

Status: proposed

## Problem

V-C 已有实证失败：DELETE 生效后，只要把删除前的 evidence 与 controls 一起恢复，正文就重新可读（`docs/engineering/v04-memory-vc-20261021-result.md`）。当前 `memory_controls` 与 evidence 默认同目录、同备份集，`history_after` 取账本最后一行 `created_at`；这份时间戳会随文件一起回滚，不能证明“控制记录至少和最新删除一样新”。设计 17 章 §5.5 要求 writer/delete/revocation 高水位在业务回滚域外，控制 ledger 也丢则只读阻断。

## Decision（proposed，未实施）

1. 新增一份与业务备份集分离的 append-only 控制权威：按 `(subject, conversation)` 单调 `seq` 记录 DELETE/CORRECT 的稳定 id、目标事件、幂等键、approval ref、writer fence，外加一条带独立 `anchor_proof` 的 head。不存正文、替换文本、embedding 或密钥。
2. 本地 `memory_controls` 继续做投影缓存；DELETE 采用 authority-first：权威 append 成功、本地投影成功才返回成功，不变式为 `local_applied_seq <= authority_head_seq`。本地成功而权威漏记视为完整性违规，阻断读，不允许用本地反写权威。
3. 恢复固定顺序：隔离 RECOVERY_ONLY → 读权威 head/records/anchor → 校验 seq/链/身份 → 对账本地 applied_seq → 重建扣留投影 → 激活。权威不可得、无记录、陈旧、错 owner、序列断裂、本地领先等全部具名阻断，读路径不得“UNKNOWN 但仍回正文”；未删经历正控仍可读。
4. 旧实例缺权威记录标 `LEGACY_HISTORY_UNKNOWN`，不能当零删除历史；从本地 ledger 导入只能标 `IMPORTED_LOCAL_LEDGER`，完整性仍 `COMPLETENESS_UNPROVEN`；空基线不能抹未知。

## 本轮限定实现（2026-10-26，方案其余部分仍未实施）

只落地两件与路径/当前性有关的事，未做恢复状态机、独立服务、writer fence 或旧实例迁移：

1. `enable_instance_memory` 必须显式接收 `--authority-db`，且必须在实例业务根之外；
   底账不再默认建在 `memory/` 内，实例业务根的恢复不会覆盖它。这只是脱离**实例**备份域，
   **同机换路径不抵抗整机回滚**，文档不得说成域外。
2. 哈希链只证一致性。受信下限由正常流程维护：真正新建的空库在底账旁保留
   `<authority>.floor.json`（seq 0），此后每次 DELETE 在 append 落库前先推进该文件；操作者不需要
   每次删除后手改数字，下限也不从待检底账自身的 head 反推。可选的
   `NYA02_MEMORY_AUTHORITY_MIN_SEQ` 仍是操作者交接值，生效下限取文件与它较大者。副本低于下限
   → `MEMORY_AUTHORITY_OLDER_THAN_TRUSTED`；没有交接、文件不可读/不可写/格式坏、或下限链摘要
   与副本不符 → `MEMORY_AUTHORITY_CURRENTNESS_UNPROVEN` / `MEMORY_AUTHORITY_HANDOFF_INVALID`。
   以上全部阻断读取与新 DELETE。既有库缺可信历史时**不会**被初始化成 0；只有文件此前不存在且
   确为空库才写零基线。未配置底账的旧实例保持原路径，不自动纳管。下限文件与底账同域，
   同域整体回滚仍不在承诺内。

   2026-10-28 补：在既有 authority/floor 之上已限定实现 operator 请求入口、case 协调记录与
   source 级读/准入 gate，实际范围见
   [F-153/F-154 实际范围](../../implemented/feature/2026-10-28-forget-generation-barrier.md)；
   本笔记其余设计项仍未实施。

## Alternatives considered

* **只在恢复时比较 epoch**：最强理由是复用 Supply `WriterFence` 的单调性、改动最小。否决原因：epoch 存在会一起回滚的库里时同样会退；且旧 writer 可以在恢复后继续写，必须每写校验，不能只在 restore 比一次。
* **继续用 `created_at` 当新鲜度**：最强理由是零新增存储。否决原因：时间戳随文件回滚，不能自证最新，正是历史 FAIL 的根因。
* **把权威放在同机另一目录**：最强理由是部署最省。否决原因：整机快照/root/admin 会一起回滚，不构成域外。
* **没有安全根也承诺防复活**：最强理由是产品语义好看。否决原因：只能证明“我收到过什么”，不能证明“我没被一起回滚”；没有独立新鲜度根时只能 fail-closed，不能绝对承诺。

## Consequences

* 收益：恢复旧 evidence + 旧 controls 但权威有更新 DELETE 时仍能扣留；权威不可得时具名阻断而非静默复活；旧 writer 晚到被两域拒绝。
* 代价：新增权威存储/客户端/运维；权威不可用时 DELETE 与读都受阻（可用性损失）；旧实例需要显式基线登记并接受 `HISTORICAL_DELETES_UNKNOWN`；完整派生面未核，只能按血缘扣留 + UNKNOWN。
* 细节与验收矩阵见 [V-C 控制权威方案](../../../../docs/engineering/v05-vc-control-authority-plan-20261026.md)，历史边界见 [有限核查](../../../../docs/engineering/v05-restore-authority-options-20261026.md) 与 [V-C 实证](../../../../docs/engineering/v04-memory-vc-20261021-result.md)。
* 相邻既有决定：[Memory 产品化接缝](../../implemented/feature/2026-10-27-v04-memory-product-service.md)、[手机 Memory 预检](../../implemented/feature/2026-10-24-v04-phone-life-memory-preflight.md)。
