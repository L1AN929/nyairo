# Agent Note: 0.7 第三阶段——阅读留疑 → 晚间私人小册子 → 续写 → future 到期浮现

Status: implemented

## Problem

白天读书时她会在正式阅读响应端口写下一个「没弄懂」的地方（`unclear`），但这条疑问只留在
私人 items 文档里：没有任何路径把它变成她自己拥有的作品；即使 `life_project_options`
已经能生成 `QUESTION_FROM_MATERIAL` 提案，装配层用长期存活的
`PrivateDailyItems` 读问题，而该适配器只在第一次 `load()` 时缓存文档，于是阅读中途新写下的
疑问永远读不到，提案只在进程刚启动时的快照里存在。晚间也没有一条「同一实例里把白天的疑问
续写成小册子」的可验收链；「给未来自己的问题」在到期前后也没有被证明只出现一次、且不自动
执行。相关既有决定见
[自主一天与多项目](../architecture/2026-11-02-v04-autonomous-day-later-and-multi-project.md)、
[日常核心连贯](../architecture/2026-10-26-v04-day-life-core-continuity.md)、
[阅读响应批注计划](2026-10-25-v04-reading-response-annotation-plan.md) 与
[0.7 日常连贯](2026-11-08-daily-life-continuity.md)。

## Decision

- 阅读留疑仍走既有 `subject_reading_response` 协议：脚本替身只返回
  `{"kind":"unclear","note":...}`，由 `private_daily_items.plan_page_annotation` 与同一个
  owner 版本落库；不新增写路径。
- `PrivateDailyItems.items()` / `metadata_view()` 改成从 owner 的**当前 head** 读元数据
  （`load(refresh=True)`），因为 Life 装配把适配器长期持有、每轮问「她现在有什么」。写入
  路径不变：`_commit` 仍自己重读并 CAS 预期 head。
- 生成提案的仍是 `life_project_options.build_options`；选中后由
  `private_project_service.LifeProjectOwner` 真创建作品，再走 AG-2/AR-0 与
  `ModelContinuationWriter`（脚本 transport）写下一版本。没有第二个作品库、没有手拼 artifact。
- 带对象的 due item 只保留一种可执行拼法：`_extra_opportunities` 产出 `fitem:` 候选，
  `build_options` 只把**无对象**的 due item 变成 `FUTURE_ITEM_DUE` popt；同一 artifact 的
  普通续写条目在 due 目标已被 `fitem:` 命名时让位（`FUTURE_ITEM_DUE_ALREADY_OFFERS_TARGET`），
  closeout 条目不受影响。这样即便 World 已占满 12 条，加 popt/fitem 也不会超过 AG-1 的 16 条。
- 演示的装配边界具名：为让默认 `resolve_gateway` 接到同一个 Supply owner，演示 env 显式补
  `NYAIRO_LIFE_SUBJECT`；同时显式停用 research/practice 两个可选 seam，以便主正链是
  `QUESTION_FROM_MATERIAL` 而不是 `RESEARCH_FROM_QUESTION`/`REVIEW_FROM_EXPERIENCE`。
  生产里这些 seam 接上时，同一条疑问仍受 `MAX_OPTIONS` 与上面的 due 去重约束。
- 两个 `action_intent` 副本（`components/world/service/action_intent.py` 与
  `.../bridge/action_intent.py`）在 `ARRANGE` / `TOGGLE_FIXTURE` 上保持同一份窄白名单与
  同一份严格 `validate_verb_params`，真实导入顺序不再决定词表。

## Alternatives considered

- **另建一个「小册子库」或 Life 自己的 project store**：最强理由是能让阅读疑问与晚间续写
  完全不依赖 Supply owner 的 grant。否决：那就是第二个作品库/第二套 owner，和既有
  ManagedArtifact 主链冲突，也让 phone/practice 看到两份互不相认的作品。
- **让脚本直接预写 artifact 内容或版本**：最强理由是演示更短、不依赖内容 transport。否决：
  作品版本必须由 owner CAS 产生，脚本替身只能决定「写什么」；预写等于伪造她的创作与版本。
- **due item 同时给 popt 和 fitem**：最强理由是两种候选语义不同（「把这件事排成项目」与
  「在这件作品上做」）。否决：同一个对象在一轮里出现两次，既花掉第二个候选名额，又让
  Decision 面对同一意图的两种拼法；无对象时才需要 popt。
- **把 World 上限从 12 再降**：最强理由是立刻给个人候选腾出位置。否决：那是全局行为改动
  且会削掉她本来能看见的世界机会；只在 due/fitem 这一处去重更小、更准。

## Consequences

- 长期存活的私人 items 适配器每轮多一次 owner 元数据读；写路径与版本语义不变。
- due item 的「项目选项」与「作品上执行」现在是互斥的两种拼法，`_extra_opportunities` 的
  文档承诺终于与 `build_options` 的实际输入一致。
- 演示里 research/practice 被显式关闭，所以它证明的是项目 owner 正链，不是 review/research
  分支；两条命令（`tests.test_v07_daily_life` 与 `scripts/v07_daily_life_demo.py`）覆盖
  unclear 落库、popt 来源、owner 创建、同 artifact 续写、future 到期前后与幂等。
