# Agent Note: v0.4 阶段 A · 本地来访端口（life_local_contact 的 transport 账本）

Status: implemented

## Problem

v0.4「有人来，也有自己的生活」这一阶段要考的是：**她在读自己的材料，普通来话不抢走活动；
明确邀请经真实选择接受/稍后/拒绝；共处有范围和结束；结束后旧材料和断点还在。**

设计 16 §4 第 1 条（分层接触）与 §5 B7（共处恢复）把它拆成四个分开的环节：注意到、愿接触、
能切换、完整交谈。"注意到"这一环此前在代码里**没有任何位置**：

- `life_co_presence.FiniteCoPresence` 已经有 codec（`parse_invitation` / `parse_end`）与
  `consider`/`begin`/`end` 三个动作，但它的输入是**调用方手里现成的 dict**。谁把"一条本地
  来话真的到了"变成那条 dict、那条 dict 落地之后**有没有被处理过**、处理的结果**存在哪**，
  一个都没有。
- 没有账本就必然出现两个具体缺陷。其一：重启之后同一个到达会被**重新问一遍模型**（"他刚才
  是不是又来过了?"），因为没有任何地方记着"这条已经问过、她已经答过了"。其二：`DEFERRED`
  无处可查，于是"当前页读完再说"这种真实延后只能退化成每个 tick 重新决定一次——设计 15 §6
  明确禁止（DEFERRED 订**特定机会**，不每 5 分钟 LLM）。
- 没有账本还会把两件必须分开的事混成一件：**收到一条消息** vs **她接纳了一次共处**。手机侧
  一旦把"我来啦"当成邀请，UI 上立刻就会出现"她接受了"的假事实。设计 15 §6 的
  `sent ≠ delivered ≠ read ≠ acknowledged ≠ understood` 与 16 §4 第 9 条（体验中的隐私）
  都要求这条界线是硬的，不是文案上的。
- 但账本**不能**变成第二 owner。关系库、亲密分、聊天域、Activity 影子状态、`LifeManager`
  全部被阶段单禁止；`life_co_presence` 已经明确"不维护共处状态机"，B 侧正在改它，
  A 不能碰也不能复制。

## Decision

只在本轮 A 的写集内实施：新增 `components/life/scripts/life_local_contact.py`（1490 行）、
两个自己的测试文件、`docs/engineering/v04-local-contact-port-result.md`、本笔记。**未改**
`life_co_presence.py` / `autonomous_life_cycle.py` / `autonomous_life_assembly.py` /
`agency_cognition_adapter.py` / `agency_decision_ag1.py` / `life_sharing_service.py`、
Phone 的任何文件（`components/phone/**`），也没动 `MANIFEST.json` 与两个
`fix_*_world_move.py`。

1. **一个实例一份有界 transport 账本，落在 Life state root**。
   `<state_root>/life_local_contact.json`，schema `chiyo.life.local_contact.v1`。
   构造签名 `LocalContactInbox(state_root, *, subject_id, home_ref, max_events=200, clock=None)`；
   `subject_id` / `home_ref` 是**实例**身份，不是客户端字段。不新开 sqlite、不新表、不迁移。
2. **只有三种入站事件，且 kind 决定它能声称什么**：
   `LOCAL_MESSAGE → OBSERVATION_ONLY`、`CO_PRESENCE_INVITATION → INVITATION`、
   `CO_PRESENCE_END → END`。两个 kind 字符串与 `life_co_presence.KIND_INVITATION/KIND_END`
   逐字相同，事件里带的 `invitation` / `end` 块字段与协议的 `parse_invitation` / `parse_end`
   逐个对齐（11 / 7 个键），B 直接喂即可；**本模块不 import `life_co_presence`**（B 拥有它）。
3. **普通消息在结构上不可能变成执行**。三层硬闸：
   payload 里带 `scope / invitation_id / requires_engagement / atomic / expires_at /
   duration_seconds / explicit_end / completion_evidence_ref` 的 message → 
   `CONTACT_FIELD_NOT_ALLOWED_FOR_KIND`；`record_result` 的 outcome 按 kind 闭集
   （message 只允许 `OBSERVED` / `UNKNOWN`），其它 → `CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY`；
   每次写入都回 `pause_requested=False`、`activity_started=False`、`owners_written=0`。
   `ALLOWED_RESULT_FIELDS` 里**根本不存在** Activity 字段，所以"顺手记个 Activity 状态"
   连门都没有。
4. **处理权用持久 claim 串行**。`claim(event_ref, *, consumer)` 发一个
   `contact_claim:<16hex>` token 并把事件转 `CLAIMED`；另一个 consumer 得到
   `CONTACT_EVENT_CLAIMED` + 现持有者（`status="claimed"`），不抢也不等；同一 consumer
   重取回同一个 token。`record_result` 在 `CLAIMED` 状态必须带匹配的 token
   （`CONTACT_CLAIM_MISMATCH`）。崩溃留下的 claim 是**可见的**（`claimed()`），
   不会被当成"还 pending"而重复处理。这条是"一次事件只处理一次"的落地方式。
5. **状态机是 transport 状态，不是 Activity 状态**：`PENDING → CLAIMED →
   PROCESSED | DEFERRED | UNKNOWN`，后两个可以被再次 claim（延后条件到了 / 对账），
   `PROCESSED` 永不再变。`DEFERRED` 必须带 `defer_condition` 或 `opportunity_ref`，
   且**不出现在 `pending()` 里**——下一个 tick 不会重新问模型；回来只能走
   `deferred_events(opportunity_ref=...)`，而且那只是读，仍要重新 claim + record。
6. **对账靠 key，不靠重新决定**。`operation_key` 缺省按规范化事件体确定性派生
   （`contact_op:<24hex>`）；同 key 同 payload → 回**原** event（`already_applied`），
   同 key 不同 payload → `CONTACT_OPERATION_KEY_CONFLICT`（零写、不覆盖）。
   `operation(key)` 给四种答案：`absent`（没落地，可重试）/ `applied` / `unknown`
   （落地但结果不明，按同 key 对账）/ `unreadable`（**不知道**，不是"没落地"）。
7. **读面绝对纯**。`pending/claimed/deferred_events/list_events/get_event/operation/
   summary/describe` 不 mkdir、不建 lock 文件、不 bump revision、不写过期推导。
   过期只在答案里给 `expired: bool`，落盘要 B 自己决定。测试用整目录 sha256 前后对比证明。
8. **坏文件按名失败，绝不静默当空**。账本缺失是 `ABSENT`（合理的新实例）；存在但读不出来是
   `CONTACT_STATE_CORRUPT` / `CONTACT_STATE_EMPTY`（半写）/ `CONTACT_STATE_SCHEMA_MISMATCH` /
   `CONTACT_STATE_SUBJECT_MISMATCH` / `CONTACT_STATE_UNREADABLE`，读面回
   `status="unreadable"` + `data=None`，写面**拒绝且不覆盖原文件**（有用例断言原文件一字未改）。
9. **正文只在两处出现**。`get_event(include_body=True)` 与 `submit` 的 `data.event`；
   `summary()` 只有计数，`list_events()` / `pending()` / `operation()` 的行只有
   `body_chars` / `body_stored` / `body_included=False`。哨兵字符串
   `PRIVATE_LOCAL_CONTACT_SENTINEL_DO_NOT_RETURN` 扫整份 JSON 断言不泄漏。
10. **身份不信客户端**。payload 里 `subject_id` / `home_ref` 与实例不同的按名拒绝
    （`CONTACT_SUBJECT_ID_MISMATCH` / `CONTACT_HOME_REF_MISMATCH`）；相同的也不采信，
    只记 `client_identity_ignored=True`。`channel` 只能是 `local_inbox`。

跨进程写入靠一个**独立** lock 文件（POSIX `flock(LOCK_EX)` / Windows
`msvcrt.locking(LK_NBLCK)`），因为账本每次都被 `os.replace`：锁在被替换的 inode 上会静默
失效，所以锁必须住在另一条文件上。这份锁只在写路径取，读路径连文件都不碰。

## Alternatives considered

- **让 `life_co_presence.FiniteCoPresence` 自己持有 inbox**。看起来最省事（一处代码），
  但它正是 B 的写集，而且会把两件事压成一件：transport（"这条到了没有 / 处理过没有"）
  与协议（"她要不要接纳 / 共处怎么结束"）。合并之后每一次 inbox 查询都要构造 runtime、
  都要过 LR-3/LR-4，读面就不可能保持纯读；`GET` 零写这条硬约束会在架构层面被破坏。
- **直接在 `life_future_items` / `co_presence.json` 里加一个 `pending_messages` 数组**。
  否决：`life_future_items` 是**她自己的"以后"意图**（title + due_at + 她选的时刻），
  把别人的到达混进去等于把"她的打算"和"别人说过的话"写在同一张表；`co_presence.json`
  已经是共处 session 记录，B 正在改它的形状，A 往里加字段就是跨写集改契约。
- **用 Activity owner 当账本**（把每条来话包成一个 Activity）。否决：这是阶段单明确禁止的
  "第二 Activity owner / Activity 影子状态"。一条普通来话不是活动，把它造成 Activity 之后
  "一次到来的事实"就会和"她有在做的事"混在同一个 SQLite 里，foreground 语义直接崩。
- **关系 / 信任 / 聊天域 owner**。否决：阶段单与设计 15 §6 都禁止关系总库与亲密分；
  本轮要做的是"有人来"的落地，不是社会系统。
- **用内存计数器记"处理过没"**。否决：重启就没了，正是要修的缺陷本身。这也排除了
  "用 `life_step_readiness` 的 TTL 缓存代替持久结果"——TTL 到期后同一条会被重新当作新的。
- **收到消息就算"她知道了 / 她接纳了"**。否决：设计 15 §6 的
  `sent ≠ delivered ≠ read ≠ acknowledged ≠ understood`、16 §4 第 9 条（体验中的隐私）。
  本模块连 `read_by_audience` 都不声称：`delivered_to_user=False`、`local_only=True`。
- **客户端传 `subject_id` / `home_ref`**（省一次实例身份注入）。否决：阶段单明确
  "来源身份由实例构造，不信客户端 uid/路径"。让客户端决定"我是谁"就是把跨实例串门
  变成一次字段填写。同理 `channel` 不收外部字符串，只收 `local_inbox`。
- **`DEFERRED` 不要求具名条件，由服务按 tick 自己重考**。否决：设计 15 §6 要求 DEFERRED
  订**特定机会**而不是每 5 分钟 LLM。本模块连定时器都没有，`deferred_events` 只是读，
  所以"自己重考"这件事在结构上不存在。
- **账本坏了就当空的、继续 append**。否决：这会把"她真的收到过什么"直接抹掉，而且掩盖了
  磁盘/半写事故。坏文件必须按名失败并且**不覆盖**（有用例断言原文件字节不变）。
- **容量满了淘汰最旧一条**。否决：淘汰是删除，且会静默改变"她曾经收到过什么"的历史。
  满了就 `CONTACT_INBOX_CAPACITY_REACHED`，压缩/归档留到有真实需要的另一次决策。
- **用 `py_compile` 检查语法**。否决：阶段单禁用（会在树里写 `__pycache__`）。用
  `ast.parse`。

## Consequences

- 收益：
  - "有人来"第一次有了**真实、有界、可重开**的位置：到达、operation key、处理结果都在
    一份 JSON 里，重启后照样读得回（`StorageAndReopenTest`）。
  - 普通来话与明确邀请在**结构上**分开：message 带 scope 被拒、message 记 `ACCEPTED` 被拒、
    返回值恒 `pause_requested=False`/`activity_started=False`。B 没有合法路径把一条消息
    变成本次共处。
  - `DEFERRED` 有具名条件且**退出 `pending`**，所以"当前页读完再说"不会再被每 tick 重问一次；
    终态 `PROCESSED` 不可复活（`CONTACT_RESULT_TERMINAL` / `CONTACT_EVENT_ALREADY_PROCESSED`）。
  - 多进程 append 与多 consumer claim 都已用**真子进程**在 Windows（`msvcrt`）与
    WSL/Linux（`flock`）上验过：4 进程 × 6 条不丢更新、`seq` 连续、两个进程抢同一条
    只有一个拿到 claim。
  - 读面纯：整目录 sha256 前后一致、没有 `.lock` 文件被创建——这直接支撑 C 侧 `GET` 的
    "不初始化、不写过期"要求。
- 代价 / 已知限制（照实写）：
  - **`ACCEPTED` 只是 transport 结论**，不是 Activity 已建立。真正起 joint Activity 仍需 B 的
    `FiniteCoPresence.begin`（Decision + Adoption）。读的人容易误解，所以文档 §1.5 / §4 都写了
    两遍。
  - **容量是硬上限且不淘汰**（200 条）。长期真实使用会撞到它，届时要么降数量、要么另开一次
    "归档/压缩"决策（那会涉及删除语义，必须单独授权）。
  - **单条正文上限 2000 字符**，超出按名拒绝而不是截断。长消息要 C 侧自己分段。
  - **账本每写一次重写整份 JSON**（含所有事件）。200 条 × 2KB 上限约 400KB，可接受；
    但如果将来容量上调，这个"整份重写"会先成为瓶颈。
  - **`expired` 只是读面推导**，本模块没有定时器也不写过期。多久失效由 B 的处境判断与
    LR-3/LR-4 决定，所以"这条还等着她回答吗"不是本模块能单独回答的问题。
  - **跨进程锁在 Windows 与 POSIX 上是两套实现**（`msvcrt` / `flock`）。两者都测了，但
    网络文件系统（NFS/SMB）上的 `msvcrt` 语义没有被本机证据覆盖；`state_root` 应落在本地盘。
  - **没有真实 socket / HTTP / 模型**：A 不发起网络、不 import 模型客户端。Phone 投递、
    B 的认知接线都由它们各自的套件与父级真实链覆盖。
  - **不 import `life_co_presence`**，只靠字符串与字段一致性 + 一个真 codec 测试
    （`CoPresenceCodecBridgeTest`）来保证对接。如果 B 改了 `parse_invitation` 需要的字段，
    那条测试会红——这是有意的耦合警报，而不是运行时依赖。
- 给 B / C 的接缝（精确签名与返回见 `docs/engineering/v04-local-contact-port-result.md` §1）：
  `LocalContactInbox(state_root, *, subject_id, home_ref, max_events=200, clock=None)`、
  `inbox_from_environment(env, *, state_root=None, subject_id=None, home_ref=None, clock=None)`，
  读面 `pending/claimed/deferred_events/list_events/get_event/operation/summary/describe`，
  写面 `submit/claim/record_result`，常量 `KIND_* / EFFECT_* / STATE_* / OUTCOME_* /
  TERMINAL_OUTCOMES / MAX_* / CHANNEL_LOCAL_INBOX / SOURCE_LOCAL / SCHEMA_VERSION`。
- 不变量：无新表 / 无新库 / 无 Memory / 无迁移 / 无删除；GET 零写；写必须过 append 锁且原子替换；
  `channel` 只有 `local_inbox`；A 的写集外文件（B 的 life 文件、C 的 `components/phone/**`、
  `MANIFEST.json`、两个 `fix_*_world_move.py`）一字未改；没有 commit / push / reset / clean。
- 回归证据（本阶段）：`Ran 42 tests … OK`，Windows 退出码 0 与 WSL 退出码 0 各自独立记录在
  `docs/engineering/v04-local-contact-port-result.md` §3.1。
