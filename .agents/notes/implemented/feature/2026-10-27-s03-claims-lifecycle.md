# Agent Note: S03 当前主张聚合 —— 观察、报告、推断的生命周期

Status: implemented

## Problem

v2 设计把 S03 定为「实际取得的观察、当前有理由相信的声明及其生命周期」（03 F-033～
F-039，原文 L12169–L12391 Q6/Q22/Q23/Q25/Q28/Q29），但树里一直没有 S03 owner：

1. `life_situation_view._knowledge_column` 只是一个**无 owner 的投影**，把调用方递进来的
   observation 分类，没有持久 current claim，也无法回答「现在到底信什么」；
2. `short_appraisal_view` 明确是**无源投影**（没有 S07 owner），不能拿它冒充信念；
3. Memory 按既有准入只保存经历/来源材料，**不拥有当前事实的裁定权**；World 拥有隐藏真相
   和位置写入，两者都不能被拿来当 S03 的第二个 owner；
4. 如果每次启动都让模型从回忆里重新猜「店几点关门、上次在哪看到这本书」，既不可重建，
   也把「有人报告过」误当成「我观察到了」，还会把冲突压成一个置信分数。

需要的是 S03 **自己的** aggregate：有限谓词、明确的 slot 身份、可重建的 current 状态、
观察/报告/推断分开、冲突并置、来源更正可传播、过期与未知合法保留。

## Decision

新增 `components/life/scripts/s03_claims.py`：一个 subject、一个 SQLite 文件、四张物理表
（`proposition_slot` / `belief_current` / `belief_evidence_edge` / `belief_event`），
事件表同时作为 outbox（`published_at` 仍为 NULL 即未消费）：

1. **身份分开**：slot 是问题 `(subject, predicate@version, scope, context,
   temporal_kind)`，规范化 tuple 唯一；claim 是某个问题的一项候选答案，opaque id +
   `[valid_from, valid_to)`。谓词只注册当前产品实际会用的有限集合（最后观察地点、商店
   某营业日闭店时刻、可能营业、售卖成员），未知谓词按名拒绝，不并入已有 slot。
2. **四种来源语义**：`OBSERVED` 只从正式 World 快照（`PhoneWorld.snapshot`）派生，
   证据带 world id/revision 和观察回执；`REPORTED` 只接受显式 `operator_report:` /
   `report:` 来源，证明「来源说过」而不证明内容为真；`INFERRED` 通过 BASIS 边绑定已有
   claim，不是新证据；推断的 basis 失效时它跟着 STALE/UNKNOWN。报告不能自证成观察，
   也不能绕过 source 身份校验。
3. **状态是离散的**：`UNKNOWN` / `SUPPORTED` / `DISPUTED` / `STALE` / `SUPERSEDED`，
   每个都带 reason。同 slot 的竞争答案并置为 DISPUTED（多值谓词的不同成员可并存）；
   没有「最后写入者胜出」，只有显式 `correct_source` 才能替换。
4. **三类时间分开**：`valid_from/valid_to`（在世界中适用的时间，未知为 NULL）、
   `observed_at`（何时观察）、`recorded_at`（何时进入本系统）。复核期限存
   `revalidate_after_seconds` 在 claim 上，`maintenance(now)` 显式写 STALE 事件；
   `current_view(now)` 只做同一投影的纯读，**不偷偷抓取、不调用模型、不写 World/Memory**。
5. **更正与撤回**：`correct_source` 只失效该 source 的 evidence edge，再重裁依赖它的
   claim；有独立证据的 claim 不被抹掉，没有替代值的旧 claim 变 UNKNOWN 且
   `payload_withheld=1`。旧 claim 的 revision 链保留在 `history`，撤回的正文在 current
   与 history 都不再出现。存在**多个 slot 的 source**且未指定 slot 时拒绝
   `S03_CORRECTION_AMBIGUOUS`，不猜。
6. **重建**：`rebuild()` 按 `aggregate_seq` 重放事件，重算三张物化表并与现表比对；读取
   路径不重放历史。事件 + edge + current + slot 在同一 `BEGIN IMMEDIATE` 事务写入，
   `operation_key` 唯一索引提供幂等，claim 更新用 revision CAS。
7. **接线**：`assemble_autonomous_life` 只在环境里存在显式 `CHIYO_S03_CLAIMS_DB` 时打开；
   新 root 的 `one_day_life.prepare` 创建 `s03/claims.sqlite3` 并写入绑定，旧 root 无绑定、
   行为不变。cycle 的 `decision_situation_context` 增加一个有界 `claims` 块（谓词、范围、
   状态、来源类别、时间），只是数据，不是候选/许可/World 事实；不新增 AG1 输出字段。
   `phone/service.py` 的 `claims` / `claim_slot` / `claim_observe` 与
   `GET /api/phone/claims`、`/claims/<slot>`、`POST /claims/observations` 走既有
   host/origin 与私人档案许可；`claim_observe` 的请求体只有 operation key，观察内容由手机
   自己向 World 正式读面取得。`chiyo_bundle/phone_tool.py` 登记
   `claims` / `claim_slot` / `claim_observe` 三个动作。

## Alternatives considered

- **把 current belief 放进 Memory** —— 否决：Memory 只拥有来源材料与经历，设计明确
  「Memory 不认证当前事实」。那会制造第二个当前状态 owner，并让删除/准入/召回规则与
  信念生命周期互相污染。
- **每次决策都让模型从 Memory 重新猜** —— 否决：不可重建、不可索引 stale、结果随采样
  漂移；也无法区分「观察到」「有人报告」「据此推断」。
- **用自由文本向量聚类当身份** —— 否决：能检索但不能承担 slot/覆盖规则，冲突与时间区间
  无法稳定判定；只有谓词注册表覆盖不足导致大量人工 unresolved 时才考虑扩展。
- **冲突取最新的那条、或压成一个置信分数** —— 否决：这正是设计禁止的最后写入胜出；
  分数会把「两个来源真的不一致」藏起来。保留并置 DISPUTED 和 reason 才可解释。
- **更正时直接原地改写旧 claim** —— 否决：会伪造历史，也会让当时的行为依据消失。只在
  claim 生命周期上追加 SUPERSEDED/INVALIDATED 事件，旧 revision 保留（撤回的 payload 扣留）。
- **让 phone 客户端提交 observation/source 内容** —— 否决：客户端无法证明自己是 World，
  等于允许 `role=world` 自证。观察入口只接受 operation key，内容由服务端正式读面产生。

## Consequences

- 新增 S03 owner 模块、一个 SQLite 文件、`s03_claims` 的 CLI/demo、phone 读面与工具动作、
  prepare 的新 root 绑定，以及最少直接相关的 `tests/test_s03_claims.py` 与
  `scripts/s03_claims_demo.py`。
- 旧 root、canonical `db.py`/`contract.py`、Memory 准入、World 位置写入、权限默认与
  AG1/AG2 协议均未改动；`short_appraisal` 仍是无 S07 owner 的有限投影，本模块不接管它。
- `scripts/s03_claims_demo.py` 用真实 Supply、真实 `PhoneWorld` 快照、真实 phone HTTP 与
  一次正式 `evaluate_once` 验证：观察有回执、两个报告冲突并置、一次源更正让旧解释退出当前
  而历史保留、推断随 basis 过期、UNKNOWN 不变成 False、同根重开状态保留、撤回不泄旧正文、
  主张不授权 World 动作也不写 Memory。0 产品模型调用、0 外网；Decision/内容为脚本替身，
  不宣称自然信念形成。
- 未覆盖：完整 Supply/Memory 来源撤权与跨备份治理、所有 predicate 的语义完备性、
  自然模型采用与 G1/24h。S03 只是「当前有理由相信什么」，不是世界真相，也不是动作许可。
- 相关旧决定：[感知只读边界](../../architecture/2026-10-16-perception-observation-boundary.md)、
  [短期评价有限投影](../../architecture/2026-10-17-short-appraisal-bounded-context.md)、
  [Memory 产品服务](2026-10-27-v04-memory-product-service.md)。
