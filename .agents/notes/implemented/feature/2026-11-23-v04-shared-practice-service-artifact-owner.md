# Agent Note: 共同实践服务落在既有 ManagedArtifact owner 上

Status: implemented

## Problem

一段“有内容的相处并留下共同作品”需要三类事实：明确点名的共享材料（含当时共享的版本）、双方真实
贡献（用户自己的话 / 她经核对的最终可见回应与真实补充）、以及可再打开的作品正文。B 已有正常 Life
链、C 已有手机入口，但两者都不能各自持有这份作品：再建聊天库/关系库/作品库就是第二 owner，
也无法和既有 CAS、不可变版本、operation 账本对齐。另外三个具体风险：客户端可自称来源（“这是她的
决定”）；共享材料被归档/换版后旧正文继续被当成可读内容；以及“误以为落了/误以为没落”的**键**
——同一件事重试要么分叉出第二段作品，要么被 owner 判成 CONFLICT。

## Decision

共同实践 = 一件普通 `ManagedArtifact`，全部读写经 `PrivateProjectService → LifeArtifactPort`。
服务 `components/life/scripts/shared_practice_service.py` 只持有“这一段”的状态与内容：

- 文档 = 第一行 `<!-- nyairo-shared-practice:v1 {json} -->` + 空行 + 正文逐字；只认第一行，
  坏标记按失败处理，任何答复都不回标记行；
- 材料引用记录 `artifact_id + version_id + revision`，每次读都经 owner 重读：读不到/归档/撤权 →
  该段所有派生文本按构造扣留（`governance.blocked`），仅移动版本 → 正文仍可读但新写被拒，
  `reconfirm_materials` 是唯一出口；共享不等于公开私有 source，excerpt 只在显式要求且可读时给；
- 两个调用方角色：`actor="user"` 只能写用户自己的话（最多把承诺挂 `PENDING`），`actor="life"`
  才能写她的回应/贡献/正文/恢复/重开，且每条都必须绑定一个经读面核对过的来源
  （`decision_ref/event_ref/action_ref/adoption_ref`）；读面没接、没有、不是它、读不出都是具名拒绝，
  落盘只存身份与状态事实，不存来源正文；
- 幂等：把 operation key 写进条目本身（`entry.operation_key`）与文档（`last_operation_key`），
  再用 intent 摘要区分“同一件事再来一次”（本地 `already_applied`，不写第二版）与“这个 key 换了
  内容”（`SHARED_PRACTICE_OPERATION_KEY_REUSED` 具名拒绝），owner 的 request digest 是第二道；
- 状态只是这件作品的 `OPEN/PAUSED/ENDED`：结束不删任何正文与贡献，重开后 `ended=None` 但保留
  `last_ended_at`，`reopen_count` 递增；Activity/意愿仍归 B；
- `context_view` 是文本优先的只读视图（真实正文 + 真实贡献 + `not_authority`），供 B 的决定与 C 的
  屏幕读取；`list_practices` 只有身份/状态/计数；
- **按原 key 的只读对账面**：`operation(operation_key)` 只问 owner 自己的 receipt / operation 记录
  （`projects.operation` → `projects.resolve_operation` → `port.read_effect_receipt` →
  `port.lookup_operation` → `port.resolve_operation` 按此顺序探测，`describe()["operation_lookup"]`
  报当前用的哪一层），回答 `APPLIED / NOT_LANDED / UNKNOWN / UNAVAILABLE`，带
  `read_only=True, resend=False`；owner 没有这个面就具名
  `SHARED_PRACTICE_OPERATION_UNSUPPORTED`，绝不自己记一份账本，也不拿这个键重发写；
- **开场幂等**：显式 `operation_key` 先问对账面再决定写不写——同 key + 同意图（标题 / 开场 /
  材料，**不含任何时间**）→ 交回原会话 `already_applied`（跨服务重建同样成立，不新建工件、不
  CONFLICT）；同 key 异意图 → `SHARED_PRACTICE_OPERATION_KEY_REUSED`，一件不写；`UNKNOWN` →
  不写、原样返回；`NOT_LANDED` → 正常创建。开场的 key+意图写在文档的 `open_operation` 里，不会被
  后续写入覆盖；没给 key 时每次派生一个新键（两次相同开场就是两段），答复标
  `operation_check="SKIPPED"`。

接口以 `docs/engineering/v04-shared-practice-service-result.md` §1 为准（签名、返回、状态、reject
码、factory、basis 核对口、`operation()` 与开场幂等），B/C 只读消费，不猜 kwargs。

## Related decisions

- [私有项目 owner 与跨进程重启](../testing/2026-10-17-cross-process-life-restart-harness.md)：不重叠；
  本服务只经 `PrivateProjectService` 说话，不新增端口。
- [分享服务与本地收件箱](2026-11-14-v04-life-sharing-service-local-inbox.md)：部分重叠（都落
  artifact），但分享是单向交付，本页是双向往来作品；互不复用状态机。
- [手机本地来访入口](2026-11-22-v04-phone-local-contact-entry.md)：手机只用 `actor="user"` 那一半。

## Alternatives considered

- **新开一张共同实践表/聊天库**：最强理由是查询与列表最方便、可按会话索引消息。否决：这就是第二个
  作品与对话 owner，绕开不可变版本、CAS 与 operation 账本，重开/对账都会失真。
- **让客户端把来源当参数直接写进条目**：最强理由是省一次读面往返。否决：客户端可自称“她的决定”，
  这条数据以后会被当成真实意愿读；必须由 owner 读面核验身份。
- **在 A 里记一份 operation 索引（key → 工件/版本）**：最强理由是便宜、不依赖端口能力、跨重建直接
  可查。否决：那是第二份账本，会与 owner 的 receipt / operation 行分叉（owner 侧的 replay、撤权、
  digest 冲突它都不知道），而且正是本轮要避免的东西；只读 owner 自己的记录才可能一致。
- **开场重试时靠 owner 的 create replay 兜底**：最强理由是不用改 A、由 owner 去重。否决：create
  的 request digest 只覆盖标题，随后补 v1 的 content 每次都带新时间 → 摘要不同 → owner 判
  `CONFLICT`，重试变成红；必须在写之前先问对账面，并让“意图”不含时间。
- **材料换版后继续允许写**：最强理由是用户体验连续、不必打断。否决：正文引用的是一个已经变了的
  版本，会写出“看起来共享过、其实没共享”的内容；改为 `MOVED` 只读 + 显式 reconfirm。

## Consequences

收益：作品可再打开、可对账、可撤权；双方贡献有来源与版本；手机永远写不了主体内容，也无法自证同意；
同一个 key 的重试不会分叉、也不会靠“再发一次写”来确认；材料治理是构造性的（不是过滤），sentinel
不外泄。代价：每次读都多一次 owner 读（材料状态 + 材料重读）；一段会话的版本数随写入线性增长；
每次显式带 key 的 open 多一次只读的对账查询；`LifeArtifactPort` 目前不暴露 `list_versions`，因此
`read_practice(include_history=True)` 在真实端口上只有 head（`complete=false`），逐版正文列表要等
owner 补能力——A 不伪造历史；没有对账面的 owner 上 A 只能标注“去重未验证”，不能假装幂等。

验证：内存 owner 契约 59 项 exit0（Windows/py3.13，含对账面与开场重放）；真实
`ManagedArtifactStore`（真 sqlite、不可变版本、真 CAS、真 receipt/operation 账本）14 项 exit0、
0 skip（WSL/py3.12，含真重启后同 key 开场重试、丢答复对账、异意图拒绝、纯读零写）；B 的 seam 29 项
exit0。详见 `docs/engineering/v04-shared-practice-service-result.md` §3。
