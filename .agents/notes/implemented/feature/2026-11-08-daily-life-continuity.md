# Agent Note: 0.7 日常连贯——默认出门、阅读恢复/完成、家具与街区候选

Status: implemented

## Problem

2026-11-08 本人批准 A/B/C 三件事，并明确本单取代旧“默认 gate 不改”和“不得给阅读暂停
合理时间条件”的限制：

- **A**：日常自主运行默认没有 `WORLD_BODY_INTENT_MUTATION_GATE=canonical`，外部环境没设时
  她即使看见可达地点也出不了门；`run-day` 还把 `feature_overrides` 传给一个不接受该参数的
  `run_segment`，并且只改父进程环境会被实例记录的特征盖回。
- **B**：`READING_WAIT_STATES=("EXHAUSTED",)` 把“读到当前版本最后一页”当成“等材料新版本”，
  读完的书会被导去等一个不会来的公版新版本；模型无 `reevaluation_hint_ref` 的阅读 PAUSE
  没有任何条件可绑，会永久锁死；阅读 Activity 也没有完成合同，最后一页只推进 checkpoint，
  不会 COMPLETE。
- **C**：家中地点只有 REST/READ/USE_PHONE/DAZE/POTTER，书桌、窗边、茶桌这些家具边的小事
  没有 typed 停留定义；默认生成的新街区在枢纽街道一次给出 11 个目的地，加上停留/身体动作
  超过 AG-1 的 16 条候选上限，整轮 `DECISION_FAILED:CANDIDATE_EXPLOSION`；停留 Activity 把
  同地点所有停留下标都写进 target_refs，续段按第一个词表项（REST）找回 plan，导致选中的
  DESK_PAUSE 完成判据永远对不上。此外：默认 seed 929711 的 coast_town **实际不生成花店**；
  默认新实例 `nyairo_day init` 会跑室内/物件准备并放置可拿物件，而演示只用了生成包、没跑
  这一步，家里除了停留没有一件真正拿得起的自己的东西；`MAX_MOVE_OPPORTUNITIES=6` 取世界
  owner 前 6 条，把主街后面的 `street_3` 截掉，海岸因此永久不可达。

## Decision

**A 日常默认 canonical，显式 off 真能覆盖。** `nyairo_day` 日常入口默认
`world_gate=canonical`，新实例的 managed marker 写入 `world_gate`；旧记录只有
`world_actions` 时按它当时的值还原（不迁移）。`--world-gate off|shadow|canonical` 是直接
写法，旧 `--world-actions/--no-world-actions` 保留为 canonical/off 别名。`run-day` 把选定的
gate 放进 `feature_overrides`，`live_day.run_segment` 接受并合并它，既传给
`map_life_environment`（resident 环境），也传给 `_ensure_services` →
`_default_service_runner` → `demo.serve_instance(world_intent_gate=...)`（真实 World 子进程）。
显式 off 作为 off 传下去，不再依赖父进程有没有设环境变量。实验入口（fast_day /
g1_real_adoption）不动。`live_day.run_segment` 自己也用同一个 `_segment_feature_overrides`
（默认 canonical，调用方显式值覆盖），所以绕开 `nyairo_day` 的直接日常启动也不再默认 off；
验收用例直接调真实的 `phone_life.map_life_environment` 与 `demo._world_child`，断言 resident
环境与 World 子进程环境里的 `WORLD_BODY_INTENT_MUTATION_GATE`，不再手工 update 一个 dict。

**B 读完即完成，未读完的阅读 PAUSE 用既有时间条件短间隔恢复。**
`READING_WAIT_STATES` 置空：EXHAUSTED 不再是“等新版本”的依据，`basis_from_readiness` 对它
返回 `None`。阅读 Activity 的 adoption spec 增加 LR-5 既有的 `PAGE_POSITION_REACHED`
完成合同（`target_page` = 当前版本页数，`book_ref` = 阅读材料）；页读的 canonical
artifact-state 证据多带一个 `page` 事实，最后一页落盘后 LR-5 自己产生
COMPLETION_ELIGIBLE 并正常 COMPLETED。模型无 hint 的阅读 PAUSE 由阅读专用默认条件
`READING_SHORT_BREAK` 绑定：沿用既有 `TIME_AT_OR_AFTER`（30 逻辑分钟，从本轮
`observed_at` 起算），不是新 kind/store/timer；LR-4 的到期扫描满足它并只发 OPEN 资格，
仍然要一次新鲜 Decision 自己选 RESUME 同一 Activity，从原 checkpoint 继续。其它活动仍然
“无 hint 的 PAUSE 绑不了任何条件”。operator `life_pause` 的含义没有改。完成合同还把
adoption 当时读到的材料版本写成 `criteria.source_version_ref`，页读证据多带一个
`source_version_ref` 事实；LR-5 的页码判据只在该版本匹配时才算数。这样旧合同不会在同
Activity 换成更长的新版本后按旧 `target_page` 提前完成；没有这个事实的旧合同行为不变。

**C 家具、物件与街区候选接进既有端口；默认供给本身保证四个地点。**
`coastal_town` 生成模板把 `bookstore`/`cafe`/`beach`/`flower_shop` 从加权 `slot_pool`
挪进 `fixed_slots`，所以每个默认新实例（含 seed 929711）都真的生成这四个地点；书店自带的
那页 `reading` 仍从 `fixed_slots + slot_pool` 收进 `content.json`。home 增加
`DESK_PAUSE`/`WINDOW_PAUSE`/`TEA_PAUSE` 三个 typed 停留（只是 `stay_at_place` 能做的事，
标题是“在书桌边坐一会儿/在窗边待一会儿/在茶桌边坐一会儿”，不冒充整理或泡茶，也不叫泡茶）；
cafe SIT、beach WALK/SIT 补具体标题与建议秒数；`flower_shop` 的 `BROWSE_STORE` 只是看看花，
不声称购买。worldgen 的 home 室内在餐桌上放一本 `book`（`portable` + `pick_up`），这是
既有 `PICK_UP`/`PLACE` 端口能真正执行、且会改变 substrate 摆放的家居小事；演示按真实
`nyairo_day init` 的同一道 `_prepare_world`/`_write_capabilities` 准备它，而不是只生成包。
Life 侧既有停留表同步登记这些 descriptor，没有新 weight、DSL、owner 或总脑。移动机会按世界
owner 自己的候选顺序取前 `MAX_MOVE_OPPORTUNITIES=7`，并把 owner 列出的相邻 **street** 保留在
这个上限内（street 是整张图的换乘点，不能全被截掉；上限本身仍是 7，所以整轮仍不超过 AG-1
的 16 条）。停留 adoption 只把**被选中的**那一个 stay target 写进 Activity 的 target_refs，
续段据此找回同一 plan；越界/闭店仍由世界包营业事实和既有可达性决定。阅读仍走正式阅读端口。
**C-购买：既有经济 owner 接进店里候选，不造第二套采购系统。**
`world_move_opportunity_source` 新增只读的 `read_store_projection`（调既有
`world_property_economy.store_shop_projection`）和 `observed_purchase_opportunities`：只有经济
owner 同时报告 `status=available`/`purchase_available=true`、人真的在 `home_goods_store`、且商品
`affordable` 时，才为目录里每件真实商品出一条 `buy:<catalog_item_id>` 候选；target 只装目录 id，
价格/余额/产权都在购买时由 owner 重新读。营业/闭店不再靠地点标题推断。移动候选名额仍以
`MAX_MOVE_OPPORTUNITIES=7` 为上限，但把默认街区里只能逛的花店和营业中的经济店作为保留项放回
名额内（不增加候选总数），否则枢纽街道的 7 条上限会先把它们截掉，街区“可逛/可买”就只剩标题；
花店是否真能逛仍由它自己的停留/营业事实决定。
Decision 选中 `buy:` 后，`AutonomousLifeCycle` 走自己的内部 checkpoint step（`action_kind=None`，
不是 World bridge 动词，也没有往任何 allowlist 加 BUY），调用既有的
`world_property_economy.purchase_item(..., main_self_verified=True, purchase_intent_id=<Decision
派生的稳定 key>)`；Life 身份就是 MainSelf，没有伪造 uid/authority，也没有走 `PhoneShopping`。
owner 在同一把锁里做营业/在店/余额/目录检查并一次原子写 DEBIT+OWNED；重放同一 key 返回
`already_applied`，不二次扣款。Activity 的完成合同是 LR-5 既有的 `WORLD_FACT_MATCH`，expected
`world_fact:buy:<item>`；只有 owner 真的写了这笔交易后，`_record_purchase_progress` 才把交易 id、
实例 id、价格、余额和 OWNED 产权作为 `world_purchase_ref` 证据交给 LR-5，因此“钱扣了/产权有了”
是 Activity 完成的唯一依据。异常或不确定结果用 `find_purchase_intent(key)` 对账，找不到交易就
如实拒绝/未知，不冒充成功。花店/咖啡馆/书店没有经济目录，仍只有逛/坐/读，不生成购买候选。

**与本单直接相关的内部修复**：`candidate_sources_ag0` 不再把
`artifact:<doc>:page:<n>` 这类**位置** checkpoint 当成 artifact id 去问 owner，避免阅读
RESUME 被误判 `TARGET_SOURCE_UNREADABLE`；`_stay_adoption_spec` 只绑定被选中的停留，避免
多停留下续段认错 plan。

## Alternatives considered

- **继续默认 gate off，等世界包新版本或另开接口。** 最强理由：不动既有默认、不承担本地动作
  风险。否决：本人本单明确要求日常默认能出门，且“缺外部 env 也可出门”，等公版不满足要求。
- **只改父进程 `os.environ`，不碰 feature_overrides/子进程参数。** 最强理由：改动最小。
  否决：实例记录的 features 会覆盖父环境，World 子进程与 resident 看到的 gate 还可能不一致；
  必须把 gate 作为显式装配参数传到两处。
- **把 EXHAUSTED 保留为 WAIT，只把默认最短时间改成很短。** 最强理由：不改阅读状态机。
  否决：读完当前版本不是等未来版本，短时间也仍然是等一个不会来的版本；必须改成完成。
- **给任意无 hint 的 PAUSE 都加默认时间条件。** 最强理由：统一、简单。否决：这会替所有活动
  发明恢复未来，超出本人对“阅读专用默认条件”的授权；只对未读完的阅读开放。
- **给街道的移动机会按距离/权重排序后再截断。** 最强理由：更“合理”。否决：本单明确不新增
  weight、不强制首选；截断只保留世界 owner 自己的顺序，是候选上限下的确定性边界。
- **把新家具小事都写成自由文本或新动作机制。** 最强理由：标题可以更像“真的做了事”。
  否决：端口只做得到停留，会冒充实际效果；维持严格 typed `stay_at_place` 定义。
- **继续把花店留在 optional `slot_pool`。** 最强理由：不动既有随机供给，生成结果更多样。
  否决：默认 seed 实际就不生成花店，本人要的是“默认新街区有花店”，optional 槽保证不了。
- **阅读合同继续只记 `target_page`，把中途换版当已知边界不处理。** 最强理由：不动 LR-5。
  否决：这不是未测，而是会让旧合同在新版本上提前完成；用证据里已有的版本事实加一个可缺省
  的判据就能挡住，且不改变没有该事实的旧合同。
- **另建 Life 采购 owner，或往 World bridge/AR-0 的动词 allowlist 加一个 BUY。** 最强理由：
  购买看起来就是“世界动作”，加一个世界动词最直接。否决：那会把营业/在店/余额/幂等/产权检查
  复制成第二套权威，并扩大对外桥动词；本单只允许把既有经济 owner 的 main-self 入口接进 Life，
  不允许新 owner 或新外部协议。
- **用 `PhoneShopping` 的 local_owner 通道替 Life 下单。** 最强理由：现成 facade，改动最少。
  否决：手机身份是 `local_owner_phone`，把它标成 Life/MainSelf 的自主决定是伪造身份；Life 用
  既有 main-self 购买入口，Decision、operation key 与账本来源都保持自己的身份。

## Consequences

- 日常新实例默认可以本地移动/停留；显式 `--world-gate off` 仍会把 off 传到真实子进程。
  旧实例的 gate 记录不被迁移。实验 profile 不受影响。
- 读完当前版本会正常 COMPLETED；不再等不存在的“新材料版本”。未读完的阅读 PAUSE 会在 30
  逻辑分钟后得到 OPEN 资格，仍需新鲜 Decision 自己 RESUME，且不抢占其它前台活动。
  **已知边界**：本单只给新 adoption 的阅读 Activity 登记完成合同；没有为本单之前就已创建、
  仍停在 EXHAUSTED/ACTIVE 的旧阅读 Activity 写迁移或补登记，这类旧记录应走新的 Decision
  或单独迁移，不在本单假装已经修好。完成合同的 `target_page` 是 adoption 当时的页数，并且
  合同绑定了当时的材料版本（`source_version_ref`）；同 Activity 后来换成新版本时，旧合同的
  页证据不匹配，不会按旧页数提前完成。没有该事实的旧合同行为不变。
- 默认新生成世界（coastal_town）固定生成书店/咖啡馆/花店/海岸，并能在一条街区内完成
  “家中家具停留 → 拿起/放下自己的笔记本（substrate 真变）→ 出门 → 书店 → 阅读
  PAUSE/恢复/完成 → 走到营业的家居店 → 买下一件真实在卖的商品 → 同 key 重放不二扣”。
  `daily_neighborhood_v1` 等静态旧包没有迁移；`nagiura` 也没有改。
- 购买只发生在 `home_goods_store`：它的商品名/价格/余额来自经济 owner 的目录，闭店、不在店
  或买不起时零候选；花店/咖啡馆/书店只保留逛/坐/读，不把 BROWSE 标题冒充成买到。默认没有
  `CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED` / `CHIYO_WORLD_DAILY_RHYTHM_ENABLED` 的实例不会因为
  这条接线而突然能买——和手机实体店一样，先要开这两个 owner 开关。demo 用 owner 自己的即时
  到家模式，不启动两小时交付时序进程；生产仍由同一 owner 的 `DELIVERY_FEATURE_ENV` 决定。
- 一次观察的移动候选上限是 7，其中 owner 列出的相邻 street 始终保留（street 是换乘点），
  只能逛的花店与营业中的经济店也保留在名额内；更远/更多的地点不在该轮 offer 里，报告里
  如实区分“包内但本轮未列”与“本包未生成”。闭店地点仍零停留候选。默认 seed 在街口的一轮
  是 16 条候选（含 3 条个人机会），没有爆炸。
- 验收：`tests.test_v07_daily_life` 24 例（gate 默认/显式 off 到真实 `map_life_environment`
  与 `_world_child` 子进程环境、EXHAUSTED 不再 WAIT、阅读默认 TIME_AT_OR_AFTER、阅读完成
  合同与版本绑定、页证据带 page/源版本、家具停留定义、默认四地点生成/白天营业、家里可拿
  笔记本、闭店零候选、移动上限与相邻 street/花店/经济店保留、失败下一轮、店内购买候选的
  营业/在店/买得起门槛、购买 Activity 只按 owner 交易完成、同一 operation key 只扣一次）；
  `scripts/v07_daily_life_demo.py` 在一个新隔离根里真实驱动默认新世界、真实室内/物件准备、
  真实 Supply/World/Body/Activity/LR-2/LR-4/LR-5/AR-0，脚本替身只做 Decision 与阅读响应，
  业务时钟机械快进，依次走完家居/出门/阅读恢复/完成，再走到营业的家居店买下真实商品并复核
  同 key 重放，最后断言一次合成 provider 超时后下一轮仍真实调用。未验证：真实模型是否自然
  选择这些候选（尤其是否会选购买）、真实短日/24h、旧阅读 Activity 的迁移、浏览器呈现、真实
  两小时交付时序（demo 用 owner 的即时到家模式）。
- 相关既有决定仍有效，本笔记只替换上述两处旧限制；不重写旧理由：
  [显式条件 PAUSE](2026-10-24-paused-explicit-resume-condition.md)、
  [PAUSE 释放执行槽](2026-10-24-paused-background-single-execution.md)、
  [严格活动定义](2026-11-07-strict-activity-definition.md)、
  [手机购物与经济物件](../architecture/2026-10-20-v04-phone-shopping-owned-purchase-consume.md)、
  [街区实体店与出门](2026-10-27-world-outing-store-and-flowerbed.md)。

后续（2026-11-08 真跑后修正，不改写上面的判断）：默认新世界的裸 `bed_surface` 在
`get_environment`/REST 里是“真的在这里”，但 Resolver 额外要求经济 `basic_bed`，
导致 PLACE 被拒；已把 Resolver 对齐到同一份 `surfaces_here` 投影，并给明确拒绝的
单步 World 动作补上既有 `BLOCK` 收尾与有界实验首故障闸门。见
[PLACE 台面真相对齐与拒绝收尾](../bug-fix/2026-11-08-v07-place-surface-and-refused-step.md)。
