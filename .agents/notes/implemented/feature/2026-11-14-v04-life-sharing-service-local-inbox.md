# Agent Note: v0.4 阶段 B · 分享生命周期与本地投递（life_sharing_service）

Status: implemented

## Problem

主体已经能读自己的资料、起研究、续作品、写回顾与做法，但**「想到→愿意分享→时机合适→发出→对方实际
怎样」这条链在生活侧根本不存在**：

- **没有分享这件事的位置**。她做完一件事，只能要么什么都不说，要么把整篇私人材料交出去。阶段单与
  设计 15 §6 都明确：私人经历的完成条件是作品/环境实际变化，不是"待发动态"；后想分享要**另建候选**，
  不能倒写过去已说。
- **草稿极易被当成已发出**。没有一处能回答："这一条到底是 DRAFT、被她延后了、放弃了、过期了，还是
  真的进了本地收件箱？"如果只记一个内存计数，重启就没了，也没法解释投递事实。
- **发送与已读要分开**。设计 15 §6：`sent ≠ delivered ≠ read ≠ acknowledged ≠ understood`，沉默不等于
  受众同意。任何"发出去了"的结论都必须能被推翻。
- **来源会变**。草稿形成之后她又改了那件作品（或把它归档了）。如果发送时不核对来源版本，就会把
  "她当时想说的"当成"现在的她说的"发出去。
- **不能开新库**。阶段单禁止第二作品库 / 第二人格记忆库；可她自己的作品本来就住在 Supply 的
  `ManagedArtifact` 里，草稿如果另存一份，就是第二作品库。

## Decision

只在本轮 B 的写集内实施：新增 `components/life/scripts/life_sharing_service.py`、两个自己的测试文件、
`docs/engineering/v04-sharing-service-result.md`、本笔记。**未改** `autonomous_life_cycle.py` /
`autonomous_life_assembly.py` / `agency_cognition_adapter.py` / `agency_decision_ag1.py` /
`life_step_readiness.py`（A 的写集）、Phone 服务、alpha contact 的任何文件，也没动 `MANIFEST` 与两个
fix 脚本。

1. **一件分享 = 一件 `ManagedArtifact`（经 `PrivateProjectService`）**。`prepare_share` 走
   `create_project`，每个状态转移走 `commit_project`（CAS = `expected_head`），读面走
   `read_project` / `list_projects`。没有新表、新 sqlite、新 jsonl、进程内账本；`describe()` 明写
   `owns_no_store=True`。
2. **「版本即状态」**：状态、来源绑定、受众、渠道、递送记录都编码在这件 artifact 的**有界信封**正文里
   （首行 `nyairo.share.v1`，`text:` 之后逐字为分享文本）。于是草稿版本就是 CAS token，状态历史就是
   owner 的不可变 `artifact_versions`，重启不丢——因为它本来就不在 B 这边。
3. **必须有明确分享文本**：`share_text`（≤2000）或 `excerpt_chars`（1..400，取来源 head 的前 N 字符），
   两者都给或都不给都拒（`SHARE_TEXT_REQUIRED` / `SharingContractRefused`）。默认永远**不**拿整篇
   私人材料当分享文本。来源正文不进任何答案，只报长度；来源读不回来就 `SOURCE_NOT_FOUND` /
   `SOURCE_UNREADABLE`（照实，不猜，也不带嵌套正文）。
4. **受众与渠道是常量**：`local:owner` + `local_inbox`，其它字符串（邮箱、URL、telegram…）→
   `AUDIENCE_REFUSED` / `CHANNEL_REFUSED`，零写。没有任何外部发送路径，也没碰任何 kill switch。
5. **状态机只有四档 + 一档终态**：`DRAFT → DEFERRED / DROPPED / LOCAL_DELIVERED`、`→ EXPIRED`；
   `DROPPED` / `EXPIRED` 是终态（`SHARE_TERMINAL_STATE`），没有复活入口。`defer` 必须给
   `defer_condition` 或 `opportunity_ref`（"明确机会"），否则 `DEFER_CONDITION_REQUIRED`。服务里没有
   任何定时器：`read_share`/`list_shares` 是纯读，DEFERRED 不会按 tick 改版本、更不会叫模型。
6. **过期只是读面推导，落盘要她/ A 明确同意**：带 `expires_after_s` 的草稿过了点之后，读面报
   `EXPIRED` + `state_derived=True` 并拒绝发送（`SHARE_EXPIRED`）；要变成事实得调一次
   `expire`（`state_derived=False`）。
7. **幂等键从意图派生**：`prepare` 用 (subject, workspace, source, source_version, text digest,
   audience, channel, title)，`send_local` 用 (share_id, 上一版 head, channel, audience)。owner 按 key
   幂等回原结果 → 不会出现第二份草稿或第二条投递；`delivery_id = sha256(share_id + key)` 也稳定，
   每次写之后**重新读回**现场，`receipt` 取自读回的 head 而不是这次"打算写什么"。
8. **本地投递的诚实语义**：`send_local` 写一个 `LOCAL_DELIVERED` 版本 + 一条 receipt
   （`local_only=True`、`external_channel_sent=False`、`external_delivery_confirmed=False`、
   `read_by_audience=False`、`acknowledged=False`、`understood=False`）。LOCAL_DELIVERED **不是**外部
   SENT / DELIVERED / READ，`read_share` 每次都把这条 receipt 原样给出，不靠计数冒充。
9. **发送前两道真实校验**：来源 head 必须仍等于草稿里记的 `source_version_id` 且 ACTIVE（否则
   `stale_source`，A 要重新 prepare）；草稿 CAS 由 owner 的 `expected_head` 决定（陈旧 → `conflict`，
   回 `current_head_version_id`）。再检查信封里的受众/渠道是否还是固定值（被改过也不放行）。
10. **UNKNOWN 先对账**：复用 `PrivateProjectService._settle` → `port.resolve_operation`。证明已落地 →
    `applied` + `reconciled=True`（仍只有一条投递）；证明没落地 → `unknown` + `retryable=True`，用**同
    一个 key** 重试；说不清 → `unknown` + `retryable=False`，不动。
11. **标题前缀只是便宜的筛子，权威仍是信封**：`TITLE_PREFIX = "[share] "` 让 `list_shares` 少读无关
    artifact；正文不是 v1 信封就报 `NOT_A_SHARE_ARTIFACT` 并塞进 `ignored`，绝不猜，也不把那份正文带出来。
12. **来源可读性决定正文投影（阶段 B 定点修复）**：`read_share` / `list_shares` / `prepare_share` /
    `send_local` 共用一条规则 —— 只有 owner **真的回答了来源读取**（`source_current.state` 为
    `READABLE` / `REVISION_CHANGED` / `ARCHIVED`）时才投影那段从来源摘出来的正文；读被**拒绝**
    （`GOVERNED`，带 owner 自己的 reason，例如 `SOURCE_GOVERNED`）或来源不存在/不可达
    （`NOT_FOUND` / `UNAVAILABLE`）时，**整份返回 JSON 都不含那段正文**：`text=""`、
    `text_withheld=true`、该行没有 `text` 键、`withheld_count` 计数、`private_text_included` 变成真实的
    "这次真的没发"。identity / state / head / 来源 id 与版本 / 受众 / 渠道 / receipt / 长度一律保留，
    并且**不**把正常换版（`REVISION_CHANGED`）与已归档（`ARCHIVED`）伪称删除或治理 —— 它们仍然可读、
    正文照发、只是 `stale=true`、`lifecycle` 照实。

## Alternatives considered

- **另建一个分享表 / 分享 JSON 库（`shares.sqlite`）**。否决：这就是第二作品库（阶段单与设计 15 都
  禁止），而且会把"她说过什么"劈成两处真理源。状态放进 artifact 版本里之后，CAS、幂等、不可变历史、
  重启存活全部免费，且父级可以直接用 sqlite 复查（见 `tests/test_v04_sharing_supply_owner.py`）。
- **把草稿写进普通 `private_items` / 一件普通作品里，不加信封**。否决：手机项目屏的"修改正文"交回的
  是整段文本，会抹掉元数据；解析时也无法区分"这是分享草稿"与"她另一页作品"（研究工件踩过同一个坑）。
- **默认拿来源整篇正文当分享文本**（省得 A 传文本）。否决：阶段单明确"不能把默认整篇私人材料当分享
  文本"。只允许显式 `share_text` 或显式 `excerpt_chars` 的有界前缀。
- **`defer` 时不要求理由，由服务按 tick 自己重考**。否决：设计 15 §6 明确 DEFERRED 订**特定机会**、
  不每 5 分钟 LLM；服务里连定时器都没有，就没有"自己重考"这回事。延迟必须有 `defer_condition` /
  `opportunity_ref`，重考是 A 的处境判断。
- **过期时直接在读面上把状态写回去**（"读一次顺手 commit 一个 EXPIRED"）。否决：GET 必须零写，读一次
  改一次版本还会制造假的 CAS 冲突。所以过期只做**推导**并标 `state_derived=True`，落盘要走 `expire`。
- **把 DROPPED / EXPIRED 做成"还能再发一次"**。否决：设计 15 §6"`DROPPED` 旧 timer 不复活"。终态就是
  终态；想再说一件事，是**新的一次 prepare**。
- **复用 alpha contact 的 draft/receipt 实现（拷贝一份影子端口）**。否决：本轮写集禁止改/复制它；而且
  contact 那套是面向真实外部渠道的（transport receipt、supersession），本轮**没有**外发，拷过来只会
  造出"看起来能发"的假能力。只借词表（DRAFT/DEFERRED/DROPPED/EXPIRED、grounded excerpt、
  sent≠delivered≠read）。
- **发送时用本地预检替代 owner 的 CAS**。否决（实测踩到）：预检会让"同一 key 的超时重放"变成
  `conflict`，而正确的答案是"这次意图早就落地了、没有第二条投递"。CAS 交给 owner，重放交给 key。

## Consequences

- 收益：主体第一次能把"我做完的这件事里，有一段想给她看"落成一个**真实、有界、private** 的草稿，
  再显式地延后 / 放弃 / 过期 / 投递到本地收件箱；投递事实有 receipt、可读、重启不丢；来源换版或草稿
  被别人改过都不会误发；同一意图重放不会产生第二条投递。
- 代价 / 已知限制：
  - **没有真实外发**，也不宣称：`LOCAL_DELIVERED` 只是本地收件箱记录，`read_by_audience=False`。
  - 受众只有一个固定身份（`local:owner`）。多对象受众要等 contact 的 canonical 端口真正接进生活侧。
  - 分享件在 owner 里就是 artifact，`list_projects` / `list_artifacts` 会看到它；要干净隐藏需要 A/手机
    把 id 加进 `excluded_artifact_ids`（A 的写集，B 只给 `is_share_title()`）。
  - **已投递的分享再发一次会被拒**（`SHARE_ALREADY_DELIVERED`，附同一条 receipt）。这是有意的：
    服务不做本地的 operation 账本探测，"第二次发送"永远不可能是新投递。
  - 每次 `list_shares` 会对候选分享逐个 `read_project`（先按标题前缀筛，仍受 `MAX_LIST=100` 约束），
    不是全库索引；`truncated` 照实报。
  - 真实 Supply owner 的用例直接构造 `ManagedArtifactStore`（真 sqlite / 真 CAS / 真 operation 与
    receipt 账本），**没走** socket、**没走** Governance 预留；那两段由 Supply 自己的套件与父级真实
    生活链覆盖。该文件需要 `fcntl`，原生 Windows 上整文件 skip，本机 WSL 上 8 项通过。
- 回归（阶段 B 父级探针复现的真实缺陷，已修）：来源 artifact 读被拒绝（`rejected` + `SOURCE_GOVERNED`）
  之后，`read_share(include_text=True)` 与 `list_shares(include_text=True)` 仍把依赖来源的正文发出来
  （`detail_contains_governed_text=true` / `list_contains_governed_text=true`）。现在四个入口共用
  "只有来源真的可读才投影正文"这条规则，sentinel `PRIVATE_SHARE_SENTINEL_DO_NOT_RETURN` 扫整份 JSON：
  改前 `Ran 9 tests … FAILED (failures=4, errors=4)`，改后 `Ran 9 tests … OK`；受影响的分享服务组
  `Ran 32 tests … OK`。正常换版 / 归档仍然可读、正文照发（不制造"被删了"的假结论）。
- 不变量：无新表 / 新库 / 新 Memory；无迁移；无删除；GET 零写；写必须 CAS；`channel` 只有
  `local_inbox`；A 的五个文件、Phone 服务、alpha contact 与 `MANIFEST` 一字未改；没有 commit / push /
  reset / clean。
- 给 A 的接缝：`LifeSharingService(projects, *, clock=None)`、`sharing_from_env(env)`、
  以及 `is_share_title` / `share_title` / `share_text_title` / `parse_share_content` 等 helper 与只读常量。精确签名与返回见
  `docs/engineering/v04-sharing-service-result.md` §1。
