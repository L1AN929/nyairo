# Agent Note: v0.4 作品收尾 —— 用 owner 自己的 archive 结束一件作品，而不是替她收尾

Status: implemented

## Problem

0.4 的验收里有一条是"不可丢失：可结束的私人项目"（19:9），但树里的私文活动被**故意**写成
"没有自动完成合同"（`autonomous_life_cycle.py:5299-5308`），原因是不想让"写成功一次"自动等于
"这件作品结束了"。于是现实是：

1. **作品只能开始、不能结束。** Supply owner 本身有 `archive` / `tombstone` 生命周期操作
   （`artifact/store.py:440-496`），但 Life 全树对 `archive_artifact` 的调用点是 **0**；
   `life_artifact_port.COMMAND_OPS` 里只有 `create_artifact`/`commit_markdown`。
2. **活动结束后作品仍被当候选，且每轮被 owner 拒一次。** `_read_resume_point` 在"已绑定/历史指名"
   这条主路径上不校验 `point.resumable`（cycle `:1523-1547`），兜底路径 `_latest_resumable_work`
   才校验（`:1451`）。作品一旦非 ACTIVE，`artifact_available` 仍为真，于是每轮为一个已关闭的作品
   构造 WORK 步骤，owner 以 `artifact is not active` 拒掉。
3. **"动作成功 ≠ 活动完成"（02 S09）没有可用谓词。** AR-1 的 FILE 种类谓词表里只有
   `artifact_written_verified` / `artifact_deleted_verified`；`artifact_deleted_verified` 对应的
   "某动作把它删了"不是我们想做的那件事 —— 归档**不是删除**。

## Decision

不动任何既有语义，只补"收尾"这一条真实通路：

1. **用 owner 自己的动词。** `LifeArtifactPort.archive_artifact(artifact_id, expected_revision,
   grant_id, operation_key)`，service op 名就是 `archive_artifact`
   （`life_supply.py:230-233`），嵌套 payload 恰好 `{subject_id, artifact_id, expected_revision}`
   （`_NESTED_ARTIFACT_LIFECYCLE`，`:260`）。`tombstone` **不**暴露：它是终态且不再serve内容。
2. **CAS 用 artifact 的 revision，不是 head version。** owner 的拒绝/冲突语义原样透出：
   `CONFLICT` 带它自己的当前 revision，`NO_CHANGE` 表示"已经归档，但不是这个键干的"，
   `DENY/BLOCKED/GOVERNANCE_REFUSED` 是确定拒绝（治理层在碰 store 之前就拒）。
3. **一个意图一个键，UNKNOWN 用原键对账。** AR-0 的 `idempotency_key` 由
   `artifact_closeout.archive_idempotency_key(owner_id, revision, activity_ref)` 决定（不含
   `correlation_id`），适配器再推导 Supply `operation_key`；重放只回放 owner 的原 operation。
4. **归属三证齐全才算"本动作归档了它"**：owner 的 operation 行（kind=ARCHIVED、结果 OK/ARCHIVED、
   revision 对得上）+ effect receipt（APPLIED/ARCHIVED）+ 当前回读（ARCHIVED、同一 revision）。
   别人归档的、或只是"现在恰好是 ARCHIVED"的一律不出证据。
5. **不新增 criteria 种类、不改 LR-5 evaluator。** 完成合同用既有 `ACTION_RESULT_MATCH`，
   `required_predicate` 只能是 AR-1 的 FILE evaluator 真正会出的 `artifact_written_verified`
   （`result_settlement_ar1.py:1790`），另加 `expected_artifact_ref=<owner id>` 与
   `[SETTLED_RESULT_EVENT, ARTIFACT_CANONICAL_STATE]` 两种证据/两个权威域。归档性由"活动唯一
   步骤就是 archive + 上面的三证"承载，不靠往 criteria 里塞字符串。
   `SettlementPolicyRegistry` 只把 `artifact_archived_verified` **登记**进 FILE 的
   `supported_predicates`（该字段当前无消费者，纯词汇），evaluator 一行未改。
6. **纯计划模块 `artifact_closeout.py`**：`closeout_target` / `parse_closeout_target` /
   `build_closeout_adoption_spec` / `build_closeout_step`（+ `is_resumable_selection` /
   `archive_idempotency_key`）。不读不写 owner、无状态，cycle 侧只接线不发明。
7. **target 命名空间全字节保真。** owner 的 id 本身就带 `artifact:` 前缀（`store.py:217`），
   所以真实 target 是 `closeout:artifact:artifact:<uuid>`；解析**只**剥那一层命名空间，
   绝不剥内层前缀 —— 否则一件真实 id 以 `artifact:` 开头的作品会被静默改指到另一件。

## Alternatives considered

1. 自动把一次写入成功或no_change当作品完成：接线最少，能立即让活动终止；但两者都不是明确收尾决定，也没有归档效果证据，违背Action成功不等于Activity完成，因此不采用。
2. 使用tombstone终态：owner已有操作，退出后不会再被候选读到；但它不再serve内容，超过保留作品内容与版本历史的收尾语义，因此只暴露archive。
3. 新增LR5判据或改AR1 evaluator输出专用archive谓词：表达最直接；但现有ACTION_RESULT_MATCH与owner归属证据已能完成本包，扩大判定器不在授权内。当前保留通用结算谓词，归档性由唯一archive步骤和三证承载。
4. 只回读ARCHIVED判断成功：成本低且状态直观；但不能证明是本operation的效果，会借用别人的归档结果，因此必须核对原operation行、effect receipt与canonical revision。

## Consequences

* 一件 ACTIVE 私文被归档后：内容、全部版本、head、兄弟作品、grant、历史全留；无删除、无 tombstone。
* 收尾后 `resumable=False`、`latest_resumable()` 不再返回它 → 每轮被拒的 BLOCKED 步骤消失（C 侧还要
  接 `selected_by="NOT_RESUMABLE"` 的具名理由，本包只提供接口）。
* `list_artifacts` 仍列出 ARCHIVED 对象（owner 只过滤 TOMBSTONED）：收尾不是隐藏。
* 真实模型链、候选资格、日常一轮的收尾决策都是 C/父级的事；本包不宣称"她现在会自己收尾"。
* 若将来允许改 AR-1 evaluator，把完成谓词换成专用的 `artifact_archived_verified` 会更干净；
  现在**不**这么做，因为那等于扩大判定路径。
