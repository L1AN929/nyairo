# Agent Note: 街区世界生活整块——实体家居店、公园花圃与一条出门演示

Status: implemented

## Problem

`daily_neighborhood_v1` 原本只有家、门前街道、街口、公园、咖啡店和书店阅读角。
公园在场景里声明了长椅和树，却没有花圃；整条街区也没有一间**实体日用店**。
经济 owner 的实体柜台只存在于内建汐见市（`home_goods_store`）和别的示例世界里，
所以「从家走到公园看当地声明的东西，再到营业的实体店看货、付钱、回家收货」
在正式生活向的街区包上没有可走的地点：要么改包，要么只能拿手机虚拟店冒充到店。

同一段还卡在时间上：`PhoneWorld` / `PhoneShopping` 原先只读墙钟，`WorldBodyService`
已经支持显式业务时钟，但手机 facade 的店面投影和购买没有接这个时刻。演示若要
确定地落在营业时间、并让「买到 → 2 小时交付时窗」可复算，就不能依赖演示实际跑在几点。

## Decision

一、**世界包内容按既有格式补齐，不新增 schema。**

* `topology.json` 增加 `home_goods_store`（`kind=store`，入口区 `main`），只与
  `street_front` 相连，步行 150 秒；`street_front.reachable` 同步增加它。
* `rules.json` 给它 10–20 点营业和 `BROWSE_STORE` / `LOOK_AROUND` 两个 affordance。
* `objects.json` 增加公园的 `flowerbed_park`（`plant`，说明「只能看，不能摘」）
  以及店里的货架/收银柜台；`scene.json` 把花圃放进 `park_path` 的摆位表，给店里
  声明 `main`/`counter` 两个区域和各自的家具。
* 重新生成 `MANIFEST.json`。`components/worldgen` 的校验器原样通过，没有改加载器或格式。

二、**出门演示走正式链，脚本决定透明。**

`scripts/world_outing_demo.py` 在新隔离根里跑：真 `init_instance`、真
`WorldBodyService`（显式注入业务时钟）、正式计时旅程逐段提交（`world_journey` →
单段 owner 提交口 → gateway → Resolver → `apply_move_to` + travel 记录）、真
`PhoneWorld` / `PhoneShopping` facade、真经济门店投影/购买/交付/产权，以及
`ARRANGE` 摆放。每一段移动都有自己的 `execution_id`，到期前位置仍在段起点，
只有 owner 的 travel 记录到期后才结算到终点。

脚本的「去哪里、买哪件、摆哪里」是**具名的脚本替身决定**，只读取 owner 当场
给出的可行选项并打印理由；它不代表模型自然选择，也不发任何模型请求。钱包是
经济 owner 的虚拟初始额度，花圃/店面是仓库世界包内容，不是运行时写给验收脚本的
假数据。

三、**手机 facade 增加可选业务时钟，默认行为不变。**

`PhoneWorld` 与 `PhoneShopping` 的构造参数新增可选 `clock`：`None` 继续用墙钟，
非 `None` 时 `snapshot` / `store` / `store_purchase` 读同一个业务时刻。生产调用方
不传这个参数，行为与之前逐字一致；压缩演示只需要把同一个时钟交给服务和手机面。

四、**回归范围。**

新增 `tests/test_world_outing_content.py`，只校验包内容与路由/营业/场景摆位；
`tests/test_world_daily_opportunities.py` 里唯一一条「地点集合恰为六个」的断言
随内容增加更新为七个。真实链的验收是运行演示本身，不另造测试矩阵。

## Alternatives considered

* **直接用内建汐见市**：它已经有公园和 `home_goods_store`，看起来最省事。但内建
  世界没有世界包场景声明，花圃只能写进代码常量或运行时临时文件；这就把「当地
  声明的东西」变成了演示脚本临时造的，无法被后续正式读面按同一来源复用。
* **复制一份新世界包**：能保住原包不动，但会多出第二套街区内容，生活链的默认
  街区仍然没有店；后续维护要同时改两个包。增加一个真实地点并更新清单更直接。
* **只加手机虚拟店**：手机渠道本来就是 24 小时虚拟店，能买但不需要到店，也不受
  营业时间约束。验收要的是「到营业的实体店看货、在店付款」，用虚拟店会绕过
  `STORE_LOCATION_REQUIRED` 与营业判断，是把另一条链冒充成这条。
* **把时钟做成必需参数或从环境变量读**：会让所有既有调用方一起改，且环境变量
  无法表达「这一段演示的同一个时刻」。可选构造参数对生产是零变化，对演示是显式绑定。
* **为了凑齐组件列表启动 Supply**：这条链不涉及作品、阅读或私有事项，Supply 没有
  任何事实参与；启动它只是让名单好看。手机面用的 `PhoneWorld` / `PhoneShopping`
  正是 `PhoneService` 直接委托的两个类，没有另造旁路。

## Consequences

* `daily_neighborhood_v1` 从 6 个地点变成 7 个、从 5 条边变成 6 条；任何外部脚本
  若把「恰好 6 个地点」写死，需要跟着更新。仓库内只发现一处这样的断言。
* 世界包清单变了；这是内容变更，不是格式变更。老实例不会自动获得新地点，也不会
  被迁移。
* 演示每次在新 `/tmp/nyairo-outing-*` 根里跑，数据保留不删；服务正规关闭并释放
  租约，不碰旧实例、不联网、不生成图片、不使用真实用户数据。
* 花圃只是世界包里的静态场景声明，不是生物/气象事实，也不授予采摘；花圃没有
  拿取能力，演示会显式检查它不在可拿取列表里。
* 这条链不证明真模型会自然选择出门、购物或摆放，也不构成 G1、24 小时或完整
  生活验收；它证明的是世界位置、路线时间、当地可见物、交易/产权/交付和家中物件
  都接在同一些真实 owner 上，并能被后续正式读面看见。

相关： [手机购物与经济物件](../architecture/2026-10-20-v04-phone-shopping-owned-purchase-consume.md)、
[房间实体店/布置/灯](../feature/2026-10-22-rc1-room-living-store-arrange-light.md)、
[手机与柜台购买的接线](../feature/2026-10-21-rc1-room-phone-wiring-and-counter-purchase.md)、
[日常地点机会投影](../architecture/2026-10-17-world-daily-opportunities.md)、
[环境事件 v2 的有限跨天事实](../architecture/2026-10-25-world-ambient-v2-finite-events.md)。
