# Agent Note: 房间生活——实体店买到手、自有家具可以真的布置、房间灯有来源地喂给 Body

Status: implemented

## Problem

rc1 + 已验收 phone/worldbody 之后，世界侧「钱 / 产权 / 摆放 / 动作 / 身体」都是真东西，但「住得下来」这条链有四个具体断点：

1. **实体店只是地图上的字**：`world_property_economy.purchase_item` 只有测试调用，`shop_projection` 无活调用方；没有人能在「到店、营业中」的前提下真的买东西。手机渠道（v04）是另一条渠道，不能冒充实体店。
2. **已到货 ≠ 能用**：到货实现 `_realize_due_deliveries` 挂在无调用的 `home_items_projection` 下面（等于永远不到货），而且**从来没有「把已购家具摆进房间」的合法写**——`PICK_UP` 对 `takeable=false` 的家具一律拒绝，`materialize_purchase` 只会把手机渠道商品硬编码扔到 `home/bedroom`。也就是说：买了桌子，既到不了家，也放不进房间。
3. **桌/椅/床只是名字**：`substrate.SURFACES` 只有一块全局 `bed_surface`；没有桌面可放东西、没有「坐/躺」的物理依据；把 `desk_surface` 做成全局 id 会让两张桌子共享一块面，必然串物。
4. **灯没有 owner**：catalog 里有 `bedside_lamp`，但没有任何「声明过的光源 + 记录下来的 power」事实；Body 侧 `ENVIRONMENT.sensory_intensity` 通道一直存在却**没有生产者**（验收文档明写「sensory_load/warmth 没有权威环境输入」）。把灯做成台词、或者按钟点算日照，都是凭空造事实。
5. **「她在不在店」有两个答案**：GET 的 `store_shop_projection` 直读 `world_state.json` 的 place，而下单的 `_store_check` 走 `read_move_state`。后者返回导航节点、并硬比写死的 `world_id == "shiomi_city"`，于是打包世界（`coastal_town_929711`）上出现「GET 在店（`at_store=true`）＋ POST 503 `WORLD_LOCATION_UNAVAILABLE`」——父级真实 Edge 实测命中，钱没动。

## Decision

在 rc1 之上、只由 C 一个 writer 改 `components/world/scripts/**` 与 `components/world/service/**`（契约另写 `docs/engineering/rc1-room-living-contract.md`，冻结给 P/U）：

1. **实体店 = 地点 + 同一经济 owner**。`world_property_economy.store_shop_projection()`（纯读：在店？营业？余额？容量？）与 `store_purchase_item()`（命令：一次原子 `_save()` 写 DEBIT + OWNED 实例）复用既有钱包/目录/渠道。新增一对合法 provenance `("local_owner_store_purchase", "store_counter_home_delivery")`，**绝不**用 `main_self_verified=True` 走捷径；本地 owner 仍由服务端 uid/subject/home 校验。配送沿用既有 2h 时间窗（`DELIVERY_DURATION_SECONDS` 未改）或即时到家，`delivery` 记录机制名仍是 `temporal_home_delivery`。
2. **读永远不配送**。新公开 `realize_due_deliveries()` 只被 `WorldBodyService.tick()` 调用（幂等：已 DELIVERED 不再 due）；`home_items_projection` 去掉那条读路径上的 realize —— 上游报告里「读路径推进 delivery」的建议**明确不采纳**。快照用纯读的 `deliveries_projection()`。
3. **布置是 `ARRANGE`，不是拿起**。新 Resolver 动词 `ARRANGE{object_id,target}`，授权判据放在经济 owner（`arrange_verdict()`：OWNED + `relocatable` + 已到货 + 她在家 + 家具在家 + 目标只在本宅声明的 area），物理写放在 substrate（一次原子 save）。`takeable` 不动，床永远拿不起来。子物件（放在该家具自己桌/椅/床面上的东西）**在同一次写入里跟着搬家**，`carried` 记账，杜绝「家具走了东西还挂在空气面上」。
4. **surface 是 instance 级且是派生的**。`substrate.SURFACE_KINDS` 声明三种 surface kind（owner catalog + `pose_condition` + `rest_modes`）；`surface_id` 形如 `desk_surface:<item_instance_id>`，**只有**该实例真的有 live placement 时才算数（`surfaces_here()`），裸 `bed_surface` 只为 M13A.1 旧实例保留。桌子可放小物件并取回（走既有 PLACE/PICK_UP），椅子/床提供真实姿势与休息条件（`rest_surfaces_here()` 替换原来「只认 SURFACES 键」的 join）。
5. **灯 = 声明 + 记录的 power**。新模块 `world_room_environment`（纯派生，不建第二 store）：`FIXTURE_DECLARATIONS`（只 `bedside_lamp`）+ `derive_light()`（place/area 过滤；缺条目 = UNKNOWN）→ `light.status/power/band/since/on_seconds/reason_code`。power 落在 substrate 的 **v1.3** 新增 `fixtures` 块（唯一兼容扩展，v1/v1.1/v1.2 迁移只在内存）。新 Resolver 动词 `TOGGLE_FIXTURE{object_id,state}`：**绝对** on/off，同 state 是零写 noop，只接受已声明可通电且真的在当前 area 的灯。
6. **Body 只吃一种输入**。`environment.light.v1 = {dim:0.20, moderate:0.45, bright:0.70}` 是唯一数字交接；`body_consequence.advance_environment()` 只喂 `ENVIRONMENT.sensory_intensity`，**永不**产 `temperature_tendency`；六指标、速率、`POSE_STRAIN`、恢复门槛、信号波段一个字没改。owner tick 一个 tick **至多一次**时间推进（恢复占了光就等下一 tick）；间隔 <60s 不动；>900s（停机回来）记 `environment_gap_unknown` 且不写盘——unknown 不是 0，不补造「灯亮了一整夜」。开关 `CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED` 默认 deny、只由部署显式设置（`owned_environment()` 只回显），GET 不能自改。
7. **快照不换形状**：仍九个顶层块，`local_environment.light` 与 `objects.surfaces / owned_furniture / deliveries` 都是块内附加字段（旧契约冻结的九块形状与既有 UI 渲染不破）。Gateway 新增只读 `get_environment`。
8. **位置只有一个判据（现场返修）**。到不到店由 `world_property_economy._current_world_location()` 决定，它改为复用 `_read_world_location()` 的 **place 级只读**（直读 `world_state.json`），与 GET 同源；不再用 `world_living_skeleton.read_move_state`（节点化 + 写死 `world_id` + 会 realize 走路）。`area` 不参与、不放宽到店门、位置缺失仍 `WORLD_LOCATION_UNAVAILABLE` fail-closed。

## Alternatives considered

* **新增一个「家具搬运/布置」专用 store 或 surface 注册表文件**。最强理由：读写分离、surface 有显式主键、跨实例查询方便。否决：那就是第二份位置真相，和 placement 一定漂移；instance 级 surface 完全可以从 `placements + catalog declaration` 派生，多一份文件只会多一个不一致源。
* **沿用一条全局 `desk_surface`（像 `bed_surface` 那样）**。最强理由：改动最小、UI 一眼能认、旧测试不用动。否决：两张桌子会共享同一块面，放上去的东西会在两张桌子间「隔空移动」，这正好是本轮要禁止的假动作；因此 `desk_surface/chair_surface` 只允许 instance 形态，裸 id 只给历史上真实存在的 `bed_surface`。
* **把到货放在读路径上（`home_items_projection` / `shop_projection` 顺手 realize）**。最强理由：实现最短，UI 刷新就看到货到了。否决：GET 会写盘（配送、时间线、经验事件），刷新节奏就成了业务时钟；上游报告里的这条建议被明确否决，改为只由 owner tick 写。
* **`PICK_UP` 家具时放宽 `takeable`，或者给家具一个「可搬」特例**。最强理由：复用现成 PLACE 通路最省事。否决：那就是「把床拿到手上」的假动作，同时破坏 M15F「未声明即拒」的基石；改为新增 `ARRANGE`（授权与经济/位置绑定），`takeable` 语义原样保留。
* **灯用 blind toggle（无 state）**。最强理由：UI 一个按钮最省事。否决：重放/断线重试会把灯翻回去；绝对 state + 同 state noop 才是可审计的。
* **按钟点/日照推算房间亮度**。最强理由：不需要任何状态就能让环境有变化。否决：没有权威输入就是编造；v1 只承认「声明过的灯 + 记录下来的开关」。

## Consequences

* 可玩链成立：到店看货架 → 买（一次扣款 + 一次产权）→ 看真实配送 → 回家 `ARRANGE` 布置/重排 → 把点心 `PLACE` 到自己书桌并取回 → 坐椅子/躺床有真实姿势与休息条件 → `TOGGLE_FIXTURE` 开关自己的灯 → 刷/重启后摆放、开关、余额、后果都在。
* 代价与边界：家具**没有**「手持」形态（`takeable=false` 不变）；暂无容器/书籍内容；灯只有 `dim` 一档声明，`dim` 不触发 `sensory_load_elevated` 是正确结果（禁止为页面好看调亮或改速率）；不存在全城经济、3D、天气、温度/噪音。
* 兼容：substrate v1.3 只在内存迁移、placement 结构不变；既有 live 实例（余额 9760、卧室里已放下的点心）不需要重置，仍能继续；房间光通路默认关闭，旧 root 不会被自动开启，启用只能是部署侧显式设置 flag。
* 到货物化只由 owner tick 完成（`_settle_deliveries` 第二步）：下单/到货本身不产生 placement，tick 用与手机渠道**同一个** seam（`materialize_owned_item`）把 OWNED + 已到货 + 无 placement 的实例落到 `home/bedroom`；冲突拒绝、消费终态不复活、不写 hand、读路径不物化。到货与下一个 tick 之间的 `NOT_MATERIALIZED` 是真实短暂中间态，不再是一条永不闭合的死路。
* 房间光的观察锚点是 `max(body.last_advanced_at, light.since)`：只谈「灯亮着且我们有观察」的那段；停机 gap 只做一次有界无输入推进（不喂光、不补造整夜），把时钟带回 now，避免旧 root 开灯后被历史 gap 永久拒绝。
* 现场返修（父级真实 Edge 实测）：到店后 `GET` 在店（`at_store=true`）但首买 503 `WORLD_LOCATION_UNAVAILABLE`。根因是**位置读法分叉**：GET 走 `_read_world_location()`（直读 `world_state.json`），下单走 `_current_world_location()` → `world_living_skeleton.read_move_state()`（返回导航节点 `<place>/<area>`，且硬比写死的 `world_id == "shiomi_city"`，打包世界 `coastal_town_929711` 因此被判成「没位置」）。已改为两处共用同一 place 级只读（`read_move_state` 的写副作用一并消失），判据仍是 `place == home_goods_store`、位置缺失仍 fail-closed。原 `operation_key` 只读核对确无写入（`find_purchase_intent=None`、余额 9760、仅旧 240 点心一笔），父级可直接按原 key 重试；但**运行中的 serve 是旧进程**，需重启才带上修复。
* 已知让位点（已由 P 关闭）：`tests/test_phone_world_api.py:242` 原断言五动词精确列表，P 已改为「必须包含」判据，复跑 `Ran 9 — OK`；C 未改该文件。
* 证据边界：owner 层 + 真实 generated coastal world 回归（`tests/test_store_room_owner.py::CoastalGeneratedWorldTests`，三跳 timed travel 真走到店后 `PhoneShopping.store_purchase` 成功）；未做真实浏览器页面验收，未在 live 实例上写任何数据（只有允许的只读查询），未重启任何服务。
