# Agent Note: 同一街区的营业时段、真实阅读材料与“未售”店面边界

Status: implemented

## Problem

`daily_neighborhood_v1` 已经有咖啡店、书店阅读角和实体家居店，也已经有
`world_daily_rhythm` 的营业规则和 `world_daily_opportunities` 的 OPEN/CLOSED
投影；但这条街区包自己的内容仍然很薄：

1. 咖啡店的 `BUY_DRINK` affordance 没有任何 owner 实现——经济 owner 只有一个
   物理渠道 `home_goods_store`，咖啡店既没有商品声明，也没有可用的购买路径。
   继续把这个 affordance 当成“能点单”是在承诺一件不存在的事。
2. 书店阅读角只有“书架/阅读桌”两个通用物件，**没有一份她能真正读的正文**；
   Supply 的阅读工作台本身已经能读、能存停点、能记页注，但新根 prepare 默认
   不预置任何材料，要读只能先手工 `import`。
3. `PhoneService` 已经有可选的 `clock`，但只用于服务自己的时间戳；它构造
   `PhoneWorld` / `PhoneShopping` / `PhoneReading` 时没有把业务时钟传下去。
   压缩演示里 World 子进程读业务时钟、手机面读墙钟，同一时刻可能一个说 OPEN、
   一个说 CLOSED。
4. 世界包有 `weather_candidates`，但没有按规则演进的天气源；也没有库存 owner。
   把静态 `clear` 默认或目录条目写成“今天晴天/有 1 件库存”会伪造事实。

## Decision

一、**包内容只沿既有格式补，不新增 schema、不新增 owner。**

* `content.json` 增加 `reading_material` 条目：标题、作者、公版来源 URL、
  `license=public-domain`、65 字开篇节选和“operator 提供、不是她的创作”的说明。
  `content.json` 本来就是本地文本内容文件，校验器允许字符串字段 ≤400 字。
* `content.json` 的 `cafe` 条目增加 `menu_note`，明确“没有接入饮品售卖、
  店员不在、柜台不接单、不能点单”；`reading_point` 增加 `shelf_note`，说明
  书架上摊的是公版节选、可以读一页、不需要买下。
* `rules.json` 把咖啡店不存在的 `BUY_DRINK` 换成 `LOOK_AT_MENU`；阅读角的
  `BROWSE_BOOKS` / `READ_HERE` 描述改成“公版书/读自己的阅读材料”。营业时间
  不改：咖啡店 8–18 工作日，阅读角 10–20，实体店 10–20。
* 重新生成包内 `MANIFEST.json`；没有新增格式，`components/worldgen` 校验器通过。
  新增地点对象没有写进 `objects.json`，因为 `nyairo_day init` 的
  `with_world=True` 会用生成的室内家具替换室内地点原有对象；把内容放在
  `content.json` / `rules.json` 才能被主入口和后续读面共同看到。

二、**新根由主入口自己装配阅读材料。**

`scripts/nyairo_day.py` 新增 `_package_reading_material()`：从所选世界包的
`content.json.entries.reading_material` 读取 title/content/source，传给既有
`demo.prepare_demo(reading_title=…, reading_content=…)`。`init` 仍走真实
Supply owner 建 artifact；包内没有该条目时保持旧行为（不预置材料）。材料是
公版节选，不是 AI 生成、不是全文，也不强迫她读。

三、**手机面显式业务时钟只补漏接，不改变生产默认。**

* `PhoneService.__init__` 新增可选 `world_clock`；非 `None` 时原样传给
  `PhoneWorld` / `PhoneShopping`，并给 `PhoneReading` 一个把同一时刻转成
  ISO 字符串的适配器。`None` 时各 facade 继续用自己的墙钟默认，行为与之前一致。
* `demo.serve_instance()` 新增可选 `business_clock` / `clock_domain` /
  `clock_run_id`：World 子进程和手机面读同一个 `fastday_clock.json`；
  不传时完全是原来的真实时间路径。真实安全/超时/权限时钟不被加速。

四、**发现和边界如实投影，不伪造。**

* 咖啡店/书店没有接入售卖；书店阅读是“未售出的公版材料”，不需要付款，
  不冒充完整店铺买卖。经济目录不是库存，库存标 `NOT_MODELED`。
* 天气只有静态 `weather_candidates`，没有规则驱动的天气源；`configured_v1`
  的 `clear` 默认不当作已观测天气，标 `NOT_MODELED`。不新增 weather owner、
  sidecar 或包格式。
* 世界动作仍走正式授权/时间/幂等和 UNKNOWN；阅读仍走 Supply owner，
  `understanding` 恒 `UNKNOWN`；没有调用产品模型。

## Alternatives considered

* **给咖啡店加一个经济渠道和饮品商品**：能让“到店买一杯”立刻跑通，菜单和
  购买也能用同一份 catalog。但经济 owner 的 `_store_check` 把
  `STORE_LOCATION_ID` 固定成 `home_goods_store`，增加第二个物理渠道要改
  owner 合同、外部目录和付费语义；本单明确把这类改动列为 STOP。否决：
  保留 `LOOK_AT_MENU` + “不能点单”的诚实内容，不用手机虚拟店冒充实体购买。
* **在 `objects.json` 里加菜单牌/书对象**：场景里能直接“看见”它们，最直观。
  但 `nyairo_day init` 的室内生成会替换咖啡店/阅读角的原有 objects，新增对象
  在新根里会被删掉；为了演示而在 demo 里临时塞对象，等于把内容变成运行时假数据。
  否决：内容放进 `content.json`，由主入口按包资源装配阅读材料。
* **给阅读加一个“必须在营业中的书店”门**：能直接满足“闭店同操作拒绝”。但
  阅读工作台是“她自己的材料”，在家里也可以读；加地点门会改变所有阅读行为，
  不是本单缺的接线。否决：本单只在**店面层**验证闭店拒绝（实体店购买），
  阅读只验证 GET 零写、真读与停点持久；不把阅读工作台说成受营业门约束。
* **把天气做成按日期/季节的虚拟源**：世界包已有 `weather_candidates`，看起来
  只差一个选择器。但没有已定义规则、没有权威，随手按日期哈希会造出第二套
  天气事实，还会诱导“下雨→情绪低落/进店”的伪因果。否决：`NOT_MODELED`。
* **在 `PhoneService` 里把 `clock` 直接当世界时钟**：少加参数。但 `PhoneService`
  自己的 `clock` 返回 ISO 字符串，给 `PhoneWorld` 会拿到字符串而不是
  `datetime`；混用会悄悄改掉状态时间戳的形状。否决：新增独立 `world_clock`，
  默认 `None`，只在本单的显式演示里传。

## Consequences

* `daily_neighborhood_v1` 的 `content.json`/`rules.json` 有内容变化，包内
  `MANIFEST.json` 随之更新；旧实例不会自动获得新内容，也不会迁移。
* 新根 `nyairo_day init --world-package daily_neighborhood_v1` 现在会绑定一份
  真实 Supply 阅读 artifact（公版节选）；没有该条目的包保持原行为。
* 咖啡店不再显示 `BUY_DRINK`；它显示 `LOOK_AT_MENU`，并明确未售。想要真实
  饮品购买需要另开经济 owner 合同，本单未做。
* `scripts/neighborhood_time_shops_demo.py` 在新 `/tmp/nyairo-time-shops-*`
  根里：真 World/经济/Supply/手机 HTTP，显式 `fastday_clock.json` 模拟
  10:30 与 20:30 JST，正式计时旅程到阅读角/实体店，读一页真实公版正文并保存
  停点，闭店购买被 `STORE_CLOSED` 拒绝且钱包不变，正常停服后同根重开保留
  位置/余额/停点。每次运行保留数据、正规关闭自己的子进程。本地世界动作门只对
  本次演示进程树显式 `canonical`；生产默认仍关闭，未改任何全局权限。
* 演示不证明真模型会自然选择出门、阅读或购物，也不构成 G1、24 小时或完整
  店铺买卖验收；它是“同一街区不同时段的真实读面 + 一条真实阅读动作 + 一条
  闭店拒绝”的限定机制证据。

相关： [出门实体店/花圃](2026-10-27-world-outing-store-and-flowerbed.md)、
[手机购物与经济物件](../architecture/2026-10-20-v04-phone-shopping-owned-purchase-consume.md)、
[阅读工作台](2026-10-22-rc1-reading-owner-phone-reading.md)、
[单地点营业预期](../architecture/2026-10-19-world-opening-expectation.md)、
[环境事件 v2](../architecture/2026-10-25-world-ambient-v2-finite-events.md)。

## Verification

* `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_neighborhood_time_shops_content`
  （WSL，新 6 例）：包内 `reading_material`/`license`/来源、manifest 校验、
  咖啡店 `LOOK_AT_MENU` 且无 `BUY_DRINK`、阅读角 `READ_HERE`/10–20 点、
  `PhoneService` 显式时钟传给两个 World facade、无时钟时仍为 `None`。
  加直接相关的 `test_world_daily_opportunities`/`test_world_outing_content`/
  `test_world_opening_expectation`，共 `Ran 33, OK`。
  另跑 `tests.test_world_neighborhood_life` 出现 `KeyError: 'adoption'`
  （`life_co_presence.begin` 在 `INTERRUPTION_NOT_APPLIED_NOW` 时早退，未到
  adoption），`tests.test_phone_reading_api`/`test_phone_reading_tool` 有 1 个既有
  schema 断言失败（`phone_tool` 的 `artifact_id` 由 work_context 引入）；两者都不在
  本单写集，未在本单修复，不据此声称全库绿。
* `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/neighborhood_time_shops_demo.py`
  （WSL，新隔离根）：`exit 0`，`product_model_requests=0`；早晨三个地点 OPEN、
  晚间全部 CLOSED 且 affordances 为空；正式旅程 270+300 秒到达阅读角/实体店；
  阅读第 1/1 页 65 字公版正文，GET 前后取得/停点条目 0→0，显式停点 applied，
  `understanding=UNKNOWN`；闭店购买 `STORE_CLOSED`，钱包 10000→10000；
  同根重开后位置 `home_goods_store`、余额 10000、停点第 1 页仍在。
* 运行结果 JSON：`.agents/worker-runs/20261027-neighborhood-time-shops-run.json`；
  十行交付说明：`.agents/worker-runs/20261027-neighborhood-time-shops-result.md`。
