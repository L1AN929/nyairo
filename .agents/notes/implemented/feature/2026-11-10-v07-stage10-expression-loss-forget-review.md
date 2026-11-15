# 0.7 第十阶段：忙时有限表达、损失承接、遗忘屏障与只读恢复检视

Status: implemented

## Problem

前九阶段已经把阅读、购买、布置、睡眠、多世界、今日自选、研究、私人日记/相册、S03 与共同实践跑在
同一 root 上，但 v0.7 默认实例还缺四块可验收的端到端事实。

第一，普通本地来话虽然已有 `SocialContactPort`、`LocalMessageResponder` 和 `LocalMessageLane`，
但主 demo 没有把**本实例自己**的 responder 接起来；在真实 ACTIVE + ATOMIC 前台下的
`BOUNDARY_DEFERRED/no_pause`、回复归档、同 key 重放不二调用，以及超长/空/零预算三个具名拒绝从未在
同一条主链上被证明过。第二，做法草案可以退休，但“退休后下一正式 Decision 的 offered 句柄不含旧
method、模型实收 `withheld_methods=PRACTICE_METHOD_RETIRED`、AG-1 拒绝脚本仍点旧 handle、随后合法
Decision 继续”没有同实例证据。第三，来源归档后“不换目标、不 fallback、不续写、草稿 send 被真实
sharing owner 以 `SOURCE_ARCHIVED` 拒绝且三件零变”也没有主链证据。第四，真实 forget 屏障、公开读口
扣留、主实例经历仍可读、诚实 `WAITING_RETENTION`，以及 `runtime_recovery_review.review` 的纯读边界
（不取 flock、不重放 WAL、backup/migration 为 `NOT_IMPLEMENTED`）都没有在真实实例现场验过。

## Decision

全部接在默认主 demo 的**同一 root、同一实例、同一 Supply owner、同一份 Memory** 上
（`_run_stage10_chain(demo, booklet, booklet_bare)`），不另起第三 root、不复制旧根、不手造
canonical JSON、不改两个锚点。四段都走既有 owner 与正式 Decision 路径：

1. **三列处境与忙时回话**：直接调用真实 `life_situation_view.read_life_situation`，body 列来自本实例
   `WorldBodyReadPort.body()`，预算列来自本实例 `LifeRequestBudget.view()/check()`，knowledge 列只收
   脚本显式输入的观察。`CURRENT/UNVERIFIED/EXPIRED` 由观察自己声明的 `valid_until` 决定；带
   `mood/fatigue/body_capacity_state` 的观察一律 `INVALID` +
   `OBSERVATION_AUTHORITY_FIELD_LEAK`。读取包在既有 `_owner_tick_frozen` 窗口内，读前后必要状态文件
   字节零变。然后由正式 Decision 从**这一轮 AG-0 真正 offered 且 START 合法的真实可续写作品**里
   挑一件开工，写出真实版本（脚本只记录它实际选中的 artifact 句柄，不发明来源；若本轮真的先
   给出主小册子，那就是它，只排除阅读材料这种非作品文档），用 LR-3 自己的
   `configure_activity_attention` 把它标成 ATOMIC；普通来话经真实 `SocialContactPort`（同实例
   inbox/protocol）与真实 `LocalMessageResponder` 处理，只有最底层 `client.complete` 是脚本替身，
   预算预留走本实例 `LifeRequestBudget` 的 `BudgetedModelClient`。回复归档在 `life_local_contact`，
   `delivered_to_user=False`，Activity id/status/revision 与前台不动；同 operation_key 重放
   `already_applied`、不二调用/二句。超长=LOCAL_REPLY_TOO_LONG（不截断）、空=
   LOCAL_REPLY_EMPTY_ANSWER、零预算=LOCAL_REPLY_BUDGET_REFUSED（同一本账的合法有界上限，不覆盖历史
   预算，底层 client 0 次）。
2. **损失承接**：对前序 `PersonalPracticeService` 中真实存在的做法调用 `retire_method` CAS 写
   RETIRED；下一正式 Decision 的 practice 查询对准这条做法，模型实收 `withheld_methods` 具名
   `PRACTICE_METHOD_RETIRED`，`practice_method_refs` 不含旧 handle。脚本 Decision 故意再点旧 handle，
   由正式 AG-1 以 `INVENTED_METHOD_REF` 拒绝且 Decision 计数不变；随后不点旧 handle 的合法 Decision
   正常提交。owner 历史版本/正文仍可读，trial/revise 均 `PRACTICE_METHOD_RETIRED` 且 head 不变。
   归档直接复用第九阶段**已经由正式 Decision START/CONTINUE 写出、并已 ABANDON** 的同一个
   research_id（`_run_stage9_chain` 把它的真实 id 返回给 `_run_stage10_chain`，不按标题或列表
   顺序猜）；不新建支线、也不为归档额外补一轮 START。对该研究作品当前的 head 经正式 Decision
   `sharing.prepare` 绑草稿，owner `archive_artifact` 用合法 expected revision 落 ARCHIVED，
   head/版本/正文保留；主小册子不在这条路径上。之后对这个已归档目标续写，由既有候选/owner
   路径拒绝（WORK_NOT_CONTINUABLE 或目标已不在合法候选集），零新版、不换到主小册子。草稿
   send_local 仍到真实 sharing owner，被 `SOURCE_ARCHIVED` 拒绝，草稿 head/state/receipt、
   inbox 计数、主小册子与支线版本零变。AG-0 的 16 条全局上限按来源轮转分席，本来就不保证
   每一个作品都进入同一轮；这是合法边界，本阶段不提高上限、不改 quiet/资格门，也不要求某件
   作品必须本轮被 START。
3. **遗忘屏障与恢复检视**：合成经验通过真实 M0 `append_outcome` 写入，并显式引用本单新建的支线
   artifact。为了让既有“遗忘后旧自述按 epoch 抑制”不把主会话整段扣留，这条可处置经历住在一个**受控
   子 conversation**（同 owner、同 Memory/Supply、同一 authority 绑定），主会话的小册子经历保持可读。
   真实 `ForgetCaseStore` 使用既有 `MemoryDeletionAuthority` 先落 barrier 再做 owner 处理；当前副本
   正文被清除，公开 `LifeMemoryService`/`LifeExperiencePort` 读不到目标，主经历仍可读；同 operation_key
   重放为 DUPLICATE、case=1、authority seq 不增。请求前另存一份页一致的 evidence 副本作为真实 retention
   源；由于经验引用了支线 artifact 且本单**不**配置新的 derived owner 擦除，case 如实停在
   `WAITING_RETENTION`（`FORGET_DERIVED_SURFACES_NOT_COVERED`），不冒 COMPLETE、不把逻辑不可见说成
   物理擦除。最后 `runtime_recovery_review.review(本实例 root)` 在 `_owner_tick_frozen` 窗口内纯读：
   不取 flock、不 load_state/replay/修 WAL、不建目录，`flock_taken=False`、`flock_probed=False`、
   backup_restore/migration 均 `NOT_IMPLEMENTED`，调用前后必要状态文件字节零变。
4. **移动时域统一**：主链固定业务日 2026-11-10，但原夹具没有给 World 服务传 `clock`，
   旅行记录按 host 墙钟生成、demo 按业务钟结算，曾把一分钟室内移动算成 257 分钟。
   仅事后改 Resolver 时钟仍不够：service/gateway 会用 host 时刻在同一请求里提前判到达。
   终稿在 `Demo.build()` 首次 World 服务构造前先确定 `self.clock`，显式体感档案也按该起点准备；
   `Demo._open_gateway()` 用真实 `WorldBodyService(config, clock=lambda: self.clock.now)`，
   从 `startup()` 起使 service、其 Resolver、gateway 到达判定与 Body 业务结算同域，避免先按 host
   初始化 Body 游标再喂更早事件。`reopen_existing()` 复用同一 gateway 构造路径；
   `bind_journey_business_clock()` 把本实例 Life 侧 Journey resolver 同样绑定到该业务钟。
   租约、一次性授权 TTL、socket timeout 等安全时间仍走各自真实时钟，不改生产契约或系统时间。
   真实 MOVE 先 `IN_TRANSIT`，按 owner 路线耗时推进后才到达，不瞬移。`WorldMoveBusinessClockDomainTest`
   两条显式跨午夜回归验证固定业务晚间不受 host 次日凌晨影响，首次服务与同 root 重开都保持该时域。
   跨夜快进现在也进入 Body owner 自己的 elapsed 结算；若会话已由 owner 完成，则如实收尾而非再伪造 stop，
   不把机械推进解释成真实等待或人体恢复。
5. **集成与测试**：`_run_stage10_chain` 接在 `main()` 末尾同 root 上，输出
   `[stage10-expression] / [stage10-loss] / [stage10-forget-recovery] / [stage10-done]`；原 `[done]`、
   山地 `[other-done]`、Stage8/9 全保留。`tests/test_v07_daily_life.py` 新增第十阶段真链用例、三列
   处境的纯函数用例，以及拥挤实例定点回归（前序已有作品/回顾/研究/分享；真实 World 适配器与全部
   admitted 来源仍在，表达轮同时拿到 World 与个人候选；归档用第九阶段返回的同一个 research_id
   完成 prepare/archive/拒续/拒送，主小册子不被换成别作），原有用例一个未删。

## Alternatives considered

- **另起第三个隔离 root 跑第十阶段**：最容易隔离失败，但它不能证明“同一实例、同一 Supply owner、同一
  份 Memory、主小册子存续”这些本阶段真正要守的边界，也没有沿前序真实 method/research/booklet 对象。
  否决。
- **在 demo 里手写 canonical JSON / 直接改 DB 造 Activity、barrier 或 deleted 标记**：能快速得到想要
  的字段，却绕过了 owner 的 CAS、状态机、authority 与 gate，等于把“证据”变成夹具。否决。
- **把断言里的业务日期改成跟墙钟，或去掉跨夜断言**：最省事，也让 demo 不再依赖具体运行日，
  但它把"移动记录与业务钟错域"这个真实缺陷藏进了断言，下次换一天运行还会以别的形式出现。
  否决；改为在移动的时域源头接同一只业务钟。
- **在 demo 里 monkeypatch 全局 `datetime`/`time` 或改系统时钟**：最强理由是一次覆盖所有时刻来源；
  但安全时钟也会被一起改掉。否决；只通过本实例现有 `clock` 参数与 Journey resolver 注入业务时刻。
- **只在启动后给 Resolver 绑定业务钟，service/Body 保持 host 时钟**：最强理由是不用处理已初始化的
  Body 时间锚；但 service/gateway 随后仍用 host 时刻判到达，使第二段 MOVE 提前完成。
  否决；在新实例首次 `startup()` 之前就统一业务时域，并在重开处沿同一构造路径注入。
- **先用 host 时钟启动，再直接改 Body 持久游标以接受更早业务事件**：最强理由是可以沿用旧夹具构造；
  但这是手改 owner 状态。否决；改初始化顺序，不回写游标、阈值或状态来伪造兼容。
- **为了让 review 显示 ASSEMBLABLE 而关闭 runtime、删 WAL 或伪造空 WAL**：会破坏只读语义，把
  “现在现场是什么”换成“我们希望它是什么”。否决；`MANUAL` 来自本 demo 自己的 live lease marker，
  如实保留。
- **把忘记请求放进主 conversation**：既有 Memory 的 epoch 抑制会把遗忘前的旧自述（包括主小册子
  经历）整段扣留，直接违反“主经历前后仍可读”。改用同 owner 的受控子 conversation 承载可处置经历；
  这不是第二个实例，也没有手改 DB。
- **配置 `supply_store` 做全派生物理擦除以换 COMPLETE**：本单没有授权实现新的物理/全派生清理，且会
  动到支线 artifact；如实停在 `WAITING_RETENTION` 并说明 uncovered 来源更符合现场。
- **用同 kind 的空 World 观察适配器顶一轮，让表达/归档两个 START 只在自己的作品机会里评估**
  （`_stage10_only_own_works`）：它最强的理由是——这一轮确实只关心她自己的作品，真实居家观察会把
  世界桶塞到 8 席、个人桶只剩 8 席，跨过静默门后个人桶有 9 条真实机会，新建支线会按确定 id 顺序
  被裁掉，是否留下只取决于随机 artifact id。但这样做等于在验收轮里关闭了真实世界机会、把候选硬塞成
  自己想要的那一件，违反“不得关闭世界机会/硬塞候选”，也把“拥挤处境下还能不能归档”偷换成“在没有
  拥挤的轮里当然能归档”。真实 upper bound 本来就不保证每个作品进入同轮；归档也不需要本轮再 START
  一件新支线。**否**：改为表达轮从真实 offered 作品里挑一件、归档复用第九阶段已写出的 research_id，
  cap 保持 16 不动。

## Consequences

第十阶段主链增加真实 owner 往返与业务钟推进，但仍然是本机脚本、0 产品模型请求、0 外网，
AF_INET/INET6/DNS/create_connection 硬拒、AF_UNIX 保留。三列处境零写、忙时回话不进 Activity/不暂停
前台、做法退休保留历史、来源归档保留版本、遗忘屏障先行且公开读口扣留，这些现在都有同 root 真链
证据。边界：回复文本是脚本替身、不是真实模型自然措辞；“回应”只是本机归档，不是用户已收到；review
的 `MANUAL` 是本进程持有 live lease marker 的真实现场，不代表存在第二个全系统进程；`WAITING_RETENTION`
的 `FORGET_DERIVED_SURFACES_NOT_COVERED` 是“经验引用了支线 artifact 而本单未配置派生 owner 擦除”的
真实缺口，不是旧备份恢复失败被解决；本阶段不是 v0.7 发布、不是 G1、不是 24h、不是完整 V-C。

父级最终验收（2026-11-11）已完成：`tests.test_v07_daily_life` 串行连续三次各 **74 tests OK / exit0**，
耗时 172.712s / 172.919s / 177.017s；完整 `scripts/v07_daily_life_demo.py` **exit0**，默认世界、山地世界、
Stage8/9/10 全部结束标记齐全，真实 World/个人候选并存，未恢复空 World 绕过。处境读 54 个状态文件零写，
忙时回复幂等且不动 ATOMIC 前台，退休句柄被 AG-1 拒绝、后续合法 Decision 正常提交；精确复用 Stage9
研究作品归档，`SOURCE_ARCHIVED` 拒送且主册子不变。遗忘保持 `WAITING_RETENTION`、主经历可读；
恢复检视如实为 `MANUAL`，58 个必要状态文件零写、`flock_probed=False`、backup/migration 未实现。
只读零写以暂停并排空本实例 owner tick 的验证窗口为边界，生产后台行为未改。两个冻结锚点 SHA256
与 Stage10 施工前实际字节基线相同，原 69 条测试保留，终稿增至 74 条。

日志为 `.agents/worker-runs/20261111-v07-stage10-final-parent-unit-{1,2,3}.log` 和 `-demo.log`；
父级摘要为同目录 `20261111-v07-stage10-final-parent-result.md`。此前超时、中断、主链红灯和被否决的
空 World 方案记录保留，不将旧失败改写为成功。`.agents/worker-runs/dbg_stage10.py` 原文件留存，
仅作为中断期间的临时诊断资料，未删除、移动或作为最终验收入口执行；其用途以本段归档说明为准。
本次父级直接验收未再派 worker，产品请求 0、累计 573/1000 余 427，无旧实例根改动、commit 或 push。

相关笔记：[[2026-10-29-local-ordinary-reply-lane]]、[[atomic-ordinary-chat-boundary]]、
[[2026-10-24-loss-to-continuation-owner-bridge]]、[[2026-11-07-v04-personal-practice-workspace]]、
[[2026-10-28-forget-generation-barrier]]、[[2026-10-28-f154-typed-source-erasure-recovery]]、
[[2026-10-27-infra-runtime-recovery]]、[[2026-11-10-v07-stage9-research-private-media-claims]]。
