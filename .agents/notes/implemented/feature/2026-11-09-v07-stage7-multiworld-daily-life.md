# 0.7 第七阶段：四主题核心生活场所、主题分页读物与多世界日常链

Status: implemented

## Problem

前六阶段都在同一个默认海岸世界（`coastal_town` seed 929711）上验证生活：出门、阅读、
购买、布置、睡眠与冷重启都对，但「世界」本身仍是单一默认图。具体三个缺口：

1. **核心生活场所不保证。** `mountain_town` / `rural_farming` 的模板池里根本没有
   `bookstore` 和 `home_goods_store`；`residential_town` 的随机抽取也会漏掉
   `bookstore`/`cafe`；`coastal_town` 的 `home_goods_store` 只在加权池里。于是「从 home
   走到书店再走到家居店」这条日常链在多数主题/种子上不成立。
2. **新实例没有主题化分页读物。** 阅读材料只来自世界包 `content.json` 的
   `reading_material` 或调用方显式文本；生成世界不声明它，所以新世界没有可读的起步材料。
   另外目录里只有 `bedside_lamp` 一件可用照明、也没有第二件能放小物的桌面家具。
3. **跨日环境事件只在内建汐见市与测试里证明过。** 生成包的 `ambient.json` 只有单日模板，
   没有人证明「同一世界真实地从 2026-11-10 入睡、11-11 晨起」时 `local_date` 真的换日、
   旧日事件退场；也没有一个非海岸、非 929711 的完整日常链。

## Decision

**A 保留随机抽取，只补缺失的核心场所。** 在 `generator._ensure_core_slots` 里定义
`CORE_LOCATION_IDS = home/school/bookstore/cafe/home_goods_store`：正常 `fixed_slots +
pick_weighted` 一字不动，只有在结果缺少某个核心场所时，才把模板自己的槽位定义**追加到
末尾**（不消耗任何 RNG，追加项在既有抽取之后处理）。因此已经齐全的世界生成字节完全不变；
`coastal_town` 模板未改，seed 929711 的 7 个文档与编辑前 scratch 基线逐文档 JSON 相同，
`package_summary` 摘要不变（locations 19 / edges 18 / npcs 3 / objects 3）。
`mountain_town` 与 `rural_farming` 的 `slot_pool` 补了主题化的 `bookstore`
（`READ_HERE` + 一页主题原创文字）与 `home_goods_store`（`BROWSE_STORE`），营业时间与
其余槽位同一套生成规则；温泉/山道、水车/果园等特色槽位仍在池里随机出现。

**B 主题分页读物用显式 opt-in 选择器。** 新增
`components/life/scripts/starter_reading_library.py::select_starter_reading(seed, theme)`：
四主题各 3 篇本地原创多页文本（`%%PAGE%%` 分页），同 `(seed,theme)` 稳定、跨主题/抽样 seed
有差异（有限库允许碰撞）。`one_day_life.resolve_reading_material` 固定优先级：任何显式
`reading_content`（含空字符串）原样保留；只有显式 `starter_reading=True` 且未给文本时才选
主题读物；否则维持旧默认（无材料）。`prepare`、`demo.prepare_demo`、`nyairo_day --starter-reading`
只做同样的普通参数透传。目录补两件**真实可买可用**的家具：`warm_floor_lamp`（1200，声明为
可通电光源、`light_band=moderate`，可 `TOGGLE_FIXTURE`）与 `window_side_table`（1600，
`desk_surface` 实例桌面，可 `PLACE/PICK_UP`）；走原经济扣款/OWNED/owner tick 落地/真实
placement/房间光投影，旧 7 件商品与售价不变。`world_body_substrate` 让一个 surface kind 可有
多个 owner catalog（`owner_catalog_items`），`world_action_resolver` 的实例台面校验改为
「实例的真实 catalog 属于该 kind 的 owner 集合」——这是让第二件桌子能拥有 `desk_surface`
所必需的最小内部修正，不改变既有 `simple_desk`/`bed_surface` 行为。

**C 跨日只证明既有权威读口，不新增账本。** demo 的业务钟固定为 2026-11-10 11:00 JST
（注入时钟，不读墙钟）。入睡前与晨起后各调一次 `_observe_ambient`：用世界包自己的时区
`world_foundation.world_local_time` + `world_ambient_events.active/materialize_ambient_events`
（`observe_world` 同一次也跑一遍）——证明世界本地日期 10→11、当日集合确定性更新、旧日
event_id 全部退场；不按观察日平移作者固定的有限事件。另在单测里用合法的 v2 作者包
（`examples/worlds/daily_neighborhood_v1`）证明同一 `ambient-finite-<identity>` 跨日只换
phase、到期与 +8/+16 天都不重生。demo 末尾新增第二个世界（`mountain_town` seed 777777）
的完整链：不同小镇/街道/特色场所/起始分页读物 → 书店读（PAUSE→fresh RESUME→最后一页完成）
→ 店内买暖光落地灯与窗边小桌 → owner tick 落地 → 回家 ARRANGE 到 living、正式 off→on
（房间光 band=moderate）→ 小物件真实放到窗边桌实例桌面 → 正式 sleep_like 跨夜 → 次日
stop/standing → 环境事件换日。

## Alternatives considered

- **直接把 bookstore/home_goods_store 挪进各主题 `fixed_slots`（像第六阶段对 coastal 那样）。**
  最强理由：固定槽位最简单，保证一定生成，且不改生成器逻辑。否决：固定槽位会在加权抽取
  **之前**处理，插入新地点会改变所有种子既有地点的 RNG 读序与坐标/名称，等于把其它世界
  悄悄重排；而本单要求「已经包含核心场所的生成结果完全不动」，只能在缺失时追加。
- **给每个缺失核心都新建独立的生成器/分支，或为测试的两个 seed 打补丁。**
  最强理由：改动局部、可控。否决：那是第二套拓扑来源，遇到别的 seed 仍会缺；必须是同一
  机制、对任意合法 seed 确定性地补齐。
- **把起始读物做成世界包 `content.json` 的 `reading_material` 条目（生成时写入）。**
  最强理由：沿用既有阅读材料来源，`_package_reading_material` 已经会读它。否决：这会改变
  每个生成包的内容并让「新世界默认有读物」变成世界数据的一部分，无法表达「只在明确
  opt-in 时选主题读物」，也容易覆盖调用方的显式文本；选择器放在 Life 侧、只由显式参数触发
  才能保住旧调用与显式空字符串语义。
- **给 window_side_table 新建一个专门的 `side_table_surface` kind 来避免改 resolver。**
  最强理由：不动 resolver 与 substrate 的匹配逻辑，改动最小。否决：本单明确要求
  `window_side_table` 提供**实例级 `desk_surface`**；新建 kind 会绕开这个约定，也会让两件
  桌子在语义上被硬拆成两种面。改为让同一种 kind 声明多个合法 owner、实例 id 仍隔离，
  resolver 只做集合归属校验。
- **为跨日环境事件新建持久账本或按观察日重算日期。**
  最强理由：可以保证「每天都有新鲜事件」。否决：环境事件本就是可确定性重算的只读投影，
  新增账本会成为第二事实来源，按观察日平移也会把作者固定的有限事件伪装成滚动重生。

## Consequences

- 四个主题在任意代表 seed 下都稳定拥有 home/school/bookstore/cafe/home_goods_store 且从 home
  可达；特色随机场所与随机路网、坐标抖动、步行耗时保持。默认 `coastal_town` 929711 的完整
  包与摘要不变（测试层用既有 `package_summary` 固化）。
- 新实例可以显式获得主题分页读物（四主题各多篇、多页），进入真正的 Supply 阅读材料并走既有
  阅读端口逐页读；旧调用、默认 929711 与显式文本（含空字符串）完全兼容。
- 目录新增两件真实可用家具：暖光落地灯（可通电、可开关、房间光 band=moderate 喂 Body）与
  窗边小桌（实例 `desk_surface` 可真实放取小物）；价格 1200/1600，脚本链预算足够。
- demo 在同一海岸世界证明 2026-11-10→11-11 的环境事件换日（单日模板按世界本地日期换身份，
  旧日退场），并在第二个山地世界完整重跑日常链。删改边界：`coastal_town.json` 未改；
  两个锚点 `autonomous_life_assembly.py` / `life_sharing_service.py` 未动。
- 写集说明：除任务书点名的 world 模块外，另做了三处**必要的同缝最小接线**——
  `components/world/scripts/world_action_resolver.py`（实例台面 owner 集合校验）、
  `components/phone/demo.py`（`prepare_demo` 的 `starter_reading` 透传）、
  以及 `scripts/nyairo_day.py` 的 `--starter-reading`；三处都只影响新增路径，既有调用行为不变。
  另因货架真实多了两件商品，`tests/test_store_room_owner.py` 里两处「货架恰有 6 件」的计数
  改为 8（不是放宽断言）；该文件的打包世界用例原本在 `_walk` 用 `OPEN_NOW` 后却让
  `PhoneShopping` 读真实墙钟（JST 夜间会因闭店而红），现把同一 `OPEN_NOW` 注入 `_phone`，
  与该用例「注入时钟、不手写 world_state」的既有意图一致。
- 已知未验证：真实模型是否会自然选这些新候选（尤其购买/布置）；真实短日/24h；浏览器呈现；
  旧世界实例的迁移；demo 的业务时钟是机械推进，不冒充整夜身体恢复。
