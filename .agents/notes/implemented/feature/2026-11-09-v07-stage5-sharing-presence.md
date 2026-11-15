# 0.7 第五阶段：新作品分享/撤回与共处恢复

Status: implemented

## Problem

`shareable_sources` 只认启动时绑定的 work 与阅读材料，**当前 ACTIVE 前台实际指向的新作品**
（傍晚小册子）不在可选来源里；而分享草稿本身又住在同一个 ManagedArtifact 库，被续写候选源当成
普通作品，挤占 AG-1 的 16 条上限，甚至把 `RESUME_ACTIVITY` / 回顾选项挤出本轮。日常链也没有
证明过：prepare→defer→send 的本地收件箱投递、DROPPED 后不得再发、来源换版后发送前复核、
普通消息不暂停活动、typed 共处邀请的 WAITING→END→资格→新鲜 RESUME。

## Decision

1. **前台来源**：`AutonomousLifeCycle._foreground_share_source()` 读 owner 自己的 Activity
   记录，用与 `step_readiness` 相同的 `_readiness_target_ref` 归一（不取 `subject_refs` 首项），
   再由一次只读 `resume_point` 确认对象。排除 private items、阅读材料、research/method 引用与
   分享草稿；前台来源排第一，仍受 `limit=3` 与去重约束。`prepare` 仍必须显式 `share_text` 或
   `excerpt_chars`，来源可选不等于许可发全文。
2. **分享草稿不是作品**：`ArtifactContinuationSource._read_points` 用 B 自己的
   `life_sharing_context.is_share_title` 过滤 `[share] ` 标题（标题已在同一次 owner list 里，
   不新增读口），并记 `last_share_skip_reasons`。没有放宽 AG-1 上限、没有删任何一类候选。
3. **同实例演示**：小册子 v3 ACTIVE 后插入——正式 Decision `prepare`（显式短文本）→ DRAFT，
   `defer`（明确条件）→ DEFERRED，operator CLI 对精确 source/version 签发既有披露许可，
   `send_local` → LOCAL_DELIVERED、本地 inbox +1、receipt `local_only` 且无外发/已读/理解；
   同草稿再发由 owner 按 `SHARE_ALREADY_DELIVERED` 拒绝。另一份草稿 `drop` → DROPPED；正式
   Decision 不再 offer 它（如实拦在 Life 侧），owner 自己也以 `SHARE_TERMINAL_STATE` 拒绝；
   已投递那份仍是 LOCAL_DELIVERED，drop 不撤回已送内容。再 prepare 一份后让同一作品 CONTINUE
   换版，send 被既有来源复核以 `SOURCE_REVISION_CHANGED` 拒绝，inbox 不增。
4. **普通消息与共处**：既有本地入站在一本 ACTIVE 的小册子上投递 `LOCAL_MESSAGE`，经
   `social_contacts.tick` 的 `observe` 读到真实 communication availability，结果
   `OBSERVATION_ONLY`、`pause_requested=false`、`activity_started=false`，私人 Activity 的
   status/revision/checkpoint/前台逐项不变。零产品模型：演示显式停用普通消息的模型回复。
   typed `CO_PRESENCE_INVITATION` → `begin_from_decision` 一次正式 Decision START → AG-2
   handoff 把原作置 WAITING（条件 `wait_dep:co_presence-*`）并释放前台，joint Activity ACTIVE；
   显式 `CO_PRESENCE_END` → joint 由 LR-5 正常 COMPLETED，LR-4 只为原作开 OPEN 资格，原作仍
   WAITING；下一轮新鲜 Decision RESUME 同一 Activity/同一作品回 ACTIVE，再真实续写一版。
   负控：NO_ACTION 邀请不 park，`atomic` 邀请在 consider 阶段以
   `FOREGROUND_ATOMIC_NOT_INTERRUPTIBLE` 拒绝，都不动私人 Activity。
5. 单测补：前台来源/limit3/private items/方法/share 草稿排除、默认 sharing 与同一
   subject/workspace、prepare/defer/send/inbox/幂等、drop 拒送/来源换版拒送、普通消息不暂停、
   typed 邀请 WAITING→END→资格→fresh RESUME；正链用真实 AG-1/AG-2/Supply owner 与本地事件。

## Alternatives considered

* **把分享草稿 id 加进 `_non_work_artifact_ids`**：最强理由是与 research/method 的既有排除
  完全同一条路。否决：它要求每轮 `list_shares`（最多 100 次 owner 读）才能拿到 id；筛选
  `[share] ` 标题只需要同一次 `list_artifacts` 已读到的 title。
* **放宽 AG-1 的 16 条上限给 RESUME 留位**：最强理由是一行解决候选截断。否决：16 是既有认知
  请求硬边界（承重墙），放宽会让每轮提示词无界增长；本次只在测试里把私人 Activity 放到候选更少的
  书店，不改变产品上限，也不删除任何一类机会。
* **测试里手造 sharing service / 直接改 artifact**：最强理由是快。否决：会绕开默认
  `service_from_env`、真实 owner、披露许可与 AG-1/AG-2；本单要证明的正是默认装配与真实链路。
* **把普通消息升级成邀请或直接恢复原作**：最强理由是演示更短。否决：普通消息是
  OBSERVATION_ONLY，共处结束只开资格；恢复必须是下一轮新鲜 Decision，不能由维护脚本代拍板。

## Consequences

* 现在同实例链能看到：ACTIVE 前台新作品真的进入有界来源；DRAFT/DEFERRED/LOCAL_DELIVERED/
  DROPPED/换版拒绝都落在 owner 的真实状态与本地 inbox 上；普通消息不暂停；typed 共处把原作
  WAITING，end 只开资格，fresh RESUME 后还能续写。分享草稿不再被当成可续写作品。
* 边界：Decision/阅读响应/续写内容与评价意图仍是脚本替身；分享只有 `local:owner` /
  `local_inbox`，不宣称外部发送、已读或理解；共处是有限单前台协议，不宣称真实并发或自然社交；
  普通消息回复在本演示被显式关闭（零产品模型）。没有新 owner/分享库/收件箱/权限/持久格式。
* 验证：`tests.test_v07_daily_life` 49 tests OK；`scripts/v07_daily_life_demo.py` exit 0。
  两个 anchor（`autonomous_life_assembly.py`、`life_sharing_service.py`）未编辑。
  相关旧记录：
  [分享本地收件箱](../feature/2026-11-14-v04-life-sharing-service-local-inbox.md)、
  [可撤销披露许可](../architecture/2026-10-29-revocable-disclosure-authority.md)、
  [本地联系与共处](../feature/2026-11-21-v04-social-life-local-contact-and-co-presence.md)、
  [第四阶段 practice 接线](2026-11-09-v07-stage4-practice-review-method.md)。
