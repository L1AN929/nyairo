# Agent Note: 物件消费的 placement 与 receipt 同一次原子保存，归因只认 owner 回执

Status: implemented

## Problem

给世界加一个「吃掉一份食物」的动作，最省事的实现是删掉那个物件的 placement：`remove_object` 之后就没了，看起来干净。但删掉之后，「物件不见了」就成了唯一的证据 —— 而它恰好同时对应三种完全不同的处境：

1. 这次 execution 真的吃掉了（已提交）；
2. execution 提交了，但 Body 后果还没写（mutation 之后崩溃）；
3. 另外某个 execution 吃掉，或根本是别的东西把物件移走了。

于是恢复逻辑只要用「物件不存在」判成功，就会把 2 和 3 一起当成 1：重复给 Body 后果、把别人的消耗算到这次执行头上，或者在 prepared 之后崩溃的窗口里凭空发明一次消费。反过来，如果干脆不判，那么 ledger 显示 committed、Body 却永远收不到后果，也会长期静默漏掉一次合法的身体后果。

同时这次动作跨了「World 拥有 placement」和「Body 拥有后果」两个 owner，执行身份必须能横跨两者：一个 execution 只能产生一次消费、一次 Body 后果，重启后还能回读到同一条记录。

## Decision

消费不是「删物件」，而是一次**带收据的 owner 变更**：`world_body_substrate` schema 升到 `world.body.substrate.v1.2`，新增 `consumptions` 块；`consume_object()` 返回的**一个** state 里同时完成两件事 —— 删掉该 instance 的 placement、把 `execution_id → receipt` 写进 `consumptions`；调用方随后只做**一次** `SubstrateStore.save()`（原子替换 + fsync）。没有「先删、再补记录」的模式，因为那正是缺口所在的窗口。

回执字段（`world.consumable_contract.RECEIPT_KEYS` 是唯一真源）：`kind` / `schema_version` / `receipt_id` / `execution_id` / `object_id` / `body_id` / `actor_id` / `proposal_fingerprint`（`sha256:{action, object_id}`）/ `before_revision` / `consumed_at`（取自服务注入的时钟）/ `source`（`held` | `place_supply`）/ `placement_before`（记下被删掉的到底是什么，因为 placement map 之后不再有它）。

归因规则收敛到一处，`world_body_reconcile.consume_evidence()` 要求四项同时成立：execution id 一致、payload 一致（ledger 自己 committed 的 `placement.<id>=consumed` 必须指向回执里的同一个 object）、subject 一致（`body_id`）、物理结果一致（placement 确实已不在）。缺任何一项 → 记 `unresolved` 并写明 `reasons`，Body 一步都不动；resolver 的后置条件同理会重读 owner 文件、要求「回执匹配 + placement 已删」，两者不同时成立就落 `uncertain`，绝不升格成 committed。

保留策略：`MAX_CONSUMPTIONS = 256`。到上限时**拒绝新的消费**（`LIMIT: … never pruned`），不清旧回执 —— 清一条旧回执等于悄悄放弃一次已经发生过的 execution 的重放保护。低于上限时不做任何裁剪。

向后兼容走既有唯一升级点 `migrate_substrate()`：`v1` / `v1.1` → `v1.2` 只补空的 `consumptions`，placement / reservations / body 绑定原样带过；读入不改写磁盘（测试断言读完后文件仍是 `v1.1`），第一次真正的 owner 写入才落新 schema。

## Alternatives considered

- **只调 `remove_object()`，靠 ledger 的 committed 状态判成功**：这是最短的一行改动，也是本文 Problem 描述的全部故障来源。它把「物理缺席」当成「执行归属」，在最关键的 after-mutation-before-commit 窗口里两种状态无法区分；一旦补一次 reconcile，就会重复给 Body 后果，或把物件缺失算到一条 UNKNOWN 的执行上。否决原因不是它慢，而是它**没有证据**：ledger 只说明「打算做什么」，不说明「哪一次真的把这份东西吃掉了」。
- **把回执写进 execution ledger，而不是 substrate**：ledger 是 append-only 证据链，本身很适合记录回执，而且不用动 substrate schema。但这样 placement 的删除与回执的落盘会变成两次独立写入（ledger 追加要先有 mutation 结果），于是重新引入「物件没了但回执还没落」的窗口 —— 正是要消灭的那个。归因证据必须与它描述的那次 owner 变更**同一次持久化**，所以回执只能进 substrate。
- **建一个独立的 `consumption_ledger.jsonl`**（像 Body 后果那样的追加日志）：读起来漂亮，但它是第二份可变真相，和 placement 之间会出现两份都要维护的跨文件一致性；崩在中间时仍然无法判断哪份是对的。否决：不引入第二个 store。
- **删除回执、只留「consumed 标记」在 placement 上**（例如 `placements[id] = {"kind": "consumed"}`）：能省掉一个块，但这样每个被消费的 instance 会永久占据 placement map（`MAX_PLACEMENTS = 512` 有界），而且 placement 的语义被污染（不再是「它现在在哪」）。
- **让 reconcile 用「物件不存在 + ledger SUCCESS」判成功、只在回执存在时加一条 debug**：看似兼容旧 home（旧 home 没有回执），实际是把 fail-open 写进恢复路径。旧 home 的 CONSUME execution 只可能来自本版之后（v1.1 时代没有这个 verb），所以「没有回执」永远是真异常，不是历史遗留。故采取 fail-closed。

## Consequences

- **收益**：消费的归因变成可证明的事实而不是推断。三个崩溃窗口都有确定答案：prepared 前后崩溃 —— 没有回执、placement 还在，什么都没吃；after-mutation 崩溃 —— 回执在、placement 没了、ledger 未提交，重放只会 `uncertain`，Body 不被提前写；崩溃 A（已提交但 Body 未写）—— 重启时按匹配回执补**一次** Body 后果，再重启只报 `already_applied`。测试 `tests/test_world_consume_faults.py` 逐条覆盖，其中 test_04 主动删掉回执、保留「物件不在」的状态，确认此时 ledger 的 SUCCESS 也不会被当成消费证据。
- **代价**：substrate 多一个块、schema 升到 v1.2，所有旧 home 走一次内存内迁移（不改写磁盘，直到下一次真实写入）；回执有 256 条上界，达到后新的消费会失败而不是静默丢历史；query 与 reconcile 多一次 substrate 读。
- **已知上限**：Crash-A 补偿路径沿用既有行为 —— 保存 runtime 并返回 transition id，但不写 `transitions.jsonl`（该缺口在本包之前就存在，对所有 verb 一致，本包未顺手改）。因此本包的断言是「该 execution 的 consequence 恰好一条、transition ref 至多一条」，不声称 ref 一定已落盘。若以后要补，应在 `world_body_reconcile` 的 heal 分支统一补写，而不是只针对 CONSUME。
- **升级触发条件**：如果将来出现真实的「一份食物多口吃完」（servings）或购买/支付 owner，回执需要携带 owner 侧的份量与来源凭证；那时应在 `world_consumable_contract` 单点扩字段并同步升级 substrate schema，而不是在 placement 上做标记。
