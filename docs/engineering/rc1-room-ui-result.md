# rc1 房间与实体日用店：界面（U）结果

写集：`components/phone/web/{app.js,style.css}`、新增 `tests/test_room_living_ui.mjs`、本文件、自有 note。
没有碰 `components/world/**`、`components/phone/{server.py,service.py}`、`scripts/**`、插件、`MANIFEST.json`、
旧测试（`test_phone_ui.mjs`、`test_phone_world_ui.mjs` 只运行、未修改）与既有实例数据。
基线：`D:\nyairo-rc1-body-backend`，rc1 `41e818f` + 已验收手机/世界身体 dirty 全部保留。

## 0. 现在这条链通到哪一段（按 C/P 已落地的代码逐字段对齐）

本轮开工时 `rc1-room-living-contract.md` 还没在这个树里，我先把界面结构写好、所有字段名与动词名收在一张
适配表里；随后 C/P 的代码落进来（`world_property_economy.store_shop_projection`、
`world_phone_shopping.store()/store_purchase()`、`server.py` 的 `GET /api/phone/store` /
`POST /api/phone/store/purchases`、`world_phone_world.py` 的 `_surfaces_block/_owned_furniture_block/
_deliveries_block/_light_block` 与 `PARAM_SPEC["ARRANGE"|"TOGGLE_FIXTURE"]`、
`world_body_substrate.SURFACE_KINDS`），我按这些真实实现把消费端逐项对齐（见 §3）。

| 段 | 界面做了什么 | 依赖的 owner 事实 |
|---|---|---|
| 进店 | 商店屏渠道卡 →「实体日用店」屏：营业状态与时段、余额、在不在店里、待收货件数、账本容量、交付方式 | `store_shop_projection`（`at_store`/`availability`/`location`/`balance`/`delivery`/`pending_delivery_count`/`capacity_available`） |
| 买 | 商品详情 → 确认弹层（价格/余额/交付）→ `POST /store/purchases{catalog_item_id, operation_key}` | 同上 + 每件 `affordable` |
| 到货 | 世界屏「还在路上的东西」：`status`/`expected_delivery_at`；pending 明写「到货前不能布置、不能用」 | `deliveries_projection`（`mode`/`duration_seconds`/`items[].status`） |
| 布置 | 「自己买的家具」卡：`placement`、`delivered`、`relocatable`、`arrange_available` + 布置按钮（`ARRANGE`/`RELOCATE` 取 owner 报的那个） | `_owned_furniture_block` + `actions.verbs` |
| 台面 | 「台面与坐卧的地方」：instance 级 `surface_id`、`pose_condition`、`rest_modes`、床上已放的件数、把手里物件放上去 | `_surfaces_block`（`here[]`）+ `PLACE` |
| 灯 | 「房间的光」：`band`/`power`/`source`/`since`/`on_seconds` + 开灯/关灯（绝对状态） | `local_environment.light` + `TOGGLE_FIXTURE` |

**不是浏览器验收**：以上仅按代码字段对齐，没有在真实实例/真实浏览器上跑过（本轮不碰 live 5651、不重启、不重买）。

## 1. 文件与入口

- `components/phone/web/app.js`（唯一逻辑文件）
  - 词表：`ROOM_VERB_NAMES`、`ROOM_EXTRA_VERBS`、`ALL_WORLD_VERBS`、`ARRANGE_PARAM_KEYS=['object_id','target']`、
    `FIXTURE_PARAM_KEYS=['object_id','state']`、`LIGHT_BAND_TEXT`、`LIGHT_POWER_TEXT`、`LIGHT_STATUS_TEXT`、
    `USABLE_KIND_TEXT`、`DELIVERY_STATE_TEXT`（含 `AT_HOME_UNPLACED`）、`DELIVERY_MODE_TEXT`
    （`temporal_home_delivery`/`instant_home_delivery`）、`SURFACE_LABEL_TEXT`、`SURFACE_SUPPORT_TEXT`、`DETAIL_FIELDS`。
    旧的 `WORLD_VERBS`（5 个）、`APP_ORDER`、`HOME_ORDER` 一个字没动。
  - 纯函数：`roomLightView`、`ownedFurnitureView`、`deliveriesView`、`surfacesView`、`roomVerbFor`、
    `paramKeyMismatch`、`buildSurfacePlacementTarget`、`buildArrangeTarget`、`arrangeTargetsFor`、
    `worldArrangeOption`、`worldFixtureOption`、`storeView`、`asNumberOrNull`。
  - 运行时：`renderStore`、`renderStoreItem`、`storeItemBlocked`、`confirmStorePurchase`、`submitStorePurchase`、
    `deliveryOutcomeLines`、`shopChannelCard`、`worldRoomLightPanel`、`worldDeliveriesPanel`、
    `worldOwnedFurniturePanel`、`worldSurfacesPanel`、`worldRoomPanel`、`confirmSurfacePlace`、
    `confirmArrange`、`confirmFixture`。
- `components/phone/web/style.css`：`.channel-card/.channel-title/.channel-note/.home-shortcuts/
  .room-fixture/.room-fixture-name` + `@media (max-width:430px)` 四条窄屏规则。
- `index.html` 未改。

界面入口（主屏仍是 8 个图标，不新增 tile）：

1. 主屏 →「实体日用店」→ 货架 → 商品 → 确认购买；
2. 主屏 →「商店」→ 渠道卡「去实体日用店」（两个渠道互切，各自 `POST`）；
3. 主屏 →「房间与布置」→ 世界屏（房间的光 / 还在路上的东西 / 自己买的家具 / 台面与坐卧的地方）。

## 2. 界面侧决定

### 2.1 两个渠道不混

- 手机线上店：`GET /shop`、`POST /purchases`，scope `purchase:<catalog_item_id>`（原样）。
- 实体日用店：`GET /store`、`POST /store/purchases`，scope `store-purchase:<catalog_item_id>`。
- `refreshAfter()` 分 `purchase` / `store-purchase` 两支；实体店购买后清 store/shop/inventory/wallet +
  `invalidateWorldSnapshot()`，只在当前屏重读一次；手机购买或世界动作只**作废** `store` 缓存，不顺手发请求。

### 2.2 动词名与参数键 fail-closed

```
ROOM_VERB_NAMES = { arrange: ['ARRANGE','RELOCATE'], fixture: ['TOGGLE_FIXTURE'] }
ARRANGE_PARAM_KEYS = ['object_id','target']      // C 的 PARAM_SPEC：ARRANGE 必需 object_id + target
FIXTURE_PARAM_KEYS = ['object_id','state']       // TOGGLE_FIXTURE 必需 object_id + state(on/off)
```

- `roomVerbFor()`：只从 `data.actions.verbs` 里取**真报上来的**动词；C 现在就报 `ARRANGE`，`RELOCATE` 只是别名候选。
- `paramKeyMismatch(reported, planned)`：owner 报的键集必须**包含**界面要发的每个键；少一个就禁用 + 说明
  （同一函数吞下 owner 允许的别名，例如同时接受 `object_id` 与 `item_instance_id`，界面仍只发 `object_id`）。
- `item_instance_id`/`fixture_id` 只作为**输入别名**：`buildWorldActionPayload` 一律输出 `object_id`。

### 2.3 摆放/布置目标只用已冻结形状

`components/phone/server.py` 的 `WORLD_TARGET_FIELDS`：`room={kind,location_id,area_id}`、
`surface={kind,location_id,surface_id}`。`buildSurfacePlacementTarget()` 只产出 surface 三键；
`buildArrangeTarget()` 只接受 room/surface（container/hand 拒）。PLACE 走「房间优先、其次台面」。

### 2.4 台面是 instance 级的

`world_body_substrate.SURFACE_KINDS`：`bed_surface`（兼容裸 id）、`desk_surface`/`chair_surface`
**只以 `<kind>:<owner item_instance_id>` 形式存在**。界面原样回传 `surface_id`（例如 82 字的
`desk_surface:item-<64hex>`），两张桌子不会串；`pose_condition`/`rest_modes`/`items` 是 owner 的声明与事实，
界面只翻译不推断（没有描述性字段就显示「服务没有声明这张台面能做什么姿势」）。

### 2.5 灯：绝对状态 + unknown 不当黑

- 只发 `{object_id, state:'on'|'off'}`；`state:'toggle'` 被 payload 层拒。
- `power` 已知时同状态按钮禁用（让 owner 判 noop）；`power` 缺失时两个都可用，文案写明提交的是绝对状态。
- `light.status !== 'available'` → 没有开关按钮；`unknown` 文案「房间光未知：<原因>。未知不等于『黑的』」。
- 回执只复述 `data.fixture.{status,state}`，并固定附「灯变了不等于身体变了」。

### 2.6 到货/布置/可用

- 家具卡先看 `delivered`/`delivery_status`（`IN_TRANSIT` → 「还没到货，不能提前布置」），再看
  `relocatable`、`arrange_available`（owner 声明），最后才看世界动词可用性。
- 布置弹层写「摆好不等于现在能用」；`materialization=pending_arrange` 的回执照实转述。

## 3. 界面消费的字段（与当前代码一致）

`GET /api/phone/store` → `data`（`world_property_economy.store_shop_projection`）：
`store_id`、`channel`、`store_kind`、`currency`、`balance`、`at_store`、`location{location_id,area_id}`、
`availability{status,opens_at,closes_at,timezone}`、`purchase_available`、`reason_code`、`capacity_available`、
`pending_delivery_count`、`owned_furniture_count`、`delivery{mode,duration_seconds,placement_requires}`、
`catalog[]{catalog_item_id,name,description,price,currency,usable_kind,affordable}`。
（界面不读 `payment` 的细节；也不提供 `store_name`，标题退回 `store_id` 的标签，不编名字。）

`GET /api/phone/world` → `data`（**位置按契约 §3，房间三块是 `objects` 的块内附加字段**）：
- `objects.owned_furniture{status,arrange_available,unplaced_delivered[],items[]{item_instance_id,catalog_item_id,name,
  label,usable_kind,relocatable,surfaces[],current_location,delivered,delivery_status,placement,arrange_available}}`；
- `objects.deliveries{status,mode,duration_seconds,pending_count,pending[],delivered[],items[]{delivery_id,
  item_instance_id,status,expected_delivery_at,delivered_at}}`；
- `objects.surfaces{status,count,here[]{surface_id,surface_kind,label,owner_object_id,owner_catalog_item_id,
  location_id,area_id,pose_condition,rest_modes[],items[]},rest_here[],declared_kinds[],instance_scoped}`；
- `local_environment.light{status,power,band,band_source,source,declared_source,since,on_seconds,fixture_ids[],
  place_id,area_id,reason_code,light_source_is_declaration,temperature_or_noise_inferred}`；
- `actions.verbs[]` 里 `ARRANGE`/`RELOCATE`/`TOGGLE_FIXTURE` 的 `available`/`blocked_reason`/`params`/`candidates`；
  `TOGGLE_FIXTURE.candidates[]`（逐盏 `object_id/label/power/since/band/declared_source`）是**多盏灯**时每盏
  状态的唯一 owner 来源，界面优先用它，没有 candidates 才退回 `light.fixture_ids` + 房间聚合 `power`。

缺块的行为：`objects.deliveries`/`objects.surfaces`/`objects.owned_furniture`/`local_environment.light`
整块缺失时对应面板不渲染（旧快照外观不变）；字段缺失时显示「服务没有提供 …」，不补值、不出按钮。
顶层同名键（`data.surfaces` 等）只是为了读旧快照兜底，**不是契约要求，也没有要求 C 加平行顶层字段**。

## 4. 实际跑过的验证（真 exit code）

工作目录 `D:\nyairo-rc1-body-backend`，Node v22.19.0：

| 命令 | 结果 |
|---|---|
| `node --check components/phone/web/app.js` | exit 0 |
| `node --test tests/test_room_living_ui.mjs` | **exit 0**，21 条全过 |
| `node --test tests/test_room_living_ui.mjs tests/test_phone_world_ui.mjs tests/test_phone_ui.mjs` | **exit 0**，80 条全过（21 + 25 + 34，旧两个文件未改） |
| `node --experimental-strip-types …/verify-agent-note-tree.ts` | exit 0（`ok: 41 note(s)`） |
| `node --experimental-strip-types …/verify-agent-note-format.ts` | exit 0（`ok: 41 note(s)`） |
| `git diff --check` | exit 0 |

新套件 21 条：`node --check` 真实子进程；旧五动词不变/新动词只在 `ALL_WORLD_VERBS`/参数键常量；
房间光 available/unknown/missing；自有家具与台面（69 字 `item-`、82 字 `desk_surface:item-`、label 分离、
无声明不推断）；布置动词（`ARRANGE` vs 只报 `RELOCATE`、`available:false`、缺 `target` 挡下、pending 不可布置）；
灯（绝对状态、`toggle` 拒、别名归一、参数键不一致挡下）；目标形状与 scope；`storeView`（`at_store`/营业时段/
余额/交付模式/`affordable`/长目录 id）；stub DOM 点击链（货架只读、购买→回世界出现家具、pending 无布置按钮、
两张台面不串且发原字节、开灯绝对状态且不宣称身体变化、unknown 光无按钮无亮度数字、无动词时休息禁用、
unknown 回执用原键查询不重发、结构静态、390 窄屏静态规则）。

stub DOM 只证明接线与字段搬运；390px 那条是 CSS 静态检查（不是父级在 390 视口量 `scrollWidth`）。
真实 Edge/移动视口、键盘焦点环、滚动与像素观感仍归父级。

## 5. 边界（能做到 / 做不到）

能做到：实体店货架与确认购买（原字节 id、独立 scope、pending 不宣称到货）；自有家具卡与布置按钮
（动词名/参数键/`relocatable`/`arrange_available` 全按 owner）；instance 级台面卡片与「把手里的小物件放上去」；
房间灯 band/source/since/on_seconds 与 `on/off` 绝对状态；世界自带摆设（`local_environment.furniture`，
标注「世界自带」）与自有家具分区；跨 app 失效与不重复 fetch、读失败不 fallback mock。

做不到 / 故意不做：不画图形化房间布置（契约与快照都没有坐标）；不做容器目标、不生成书籍内容、
不做「灯亮就睡不着」这类新身体门槛；不按名字判断能力（台面「能坐/能躺」只照 `pose_condition`/`rest_modes`）；
未操作任何 live 实例（未连 `:5651`、未重买、未重置、未停服重启）；没有真实模型/支付/生产/staging/commit/push/tag/打包/`MANIFEST`。

## 6. 父级启动前需要的条件（照 C 契约 §6–§7 抄，供父级核对，界面不自己改任何 flag）

| 条件 | 默认 | 谁需要设置 | 影响界面的哪一块 |
|---|---|---|---|
| `CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED` | 既有 demo 已开 | 世界/经济既有配置 | `/store` 整屏（关了会 `FEATURE_DISABLED`） |
| `CHIYO_WORLD_LIVING_SKELETON_ENABLED` / `CHIYO_WORLD_DAILY_RHYTHM_ENABLED` | 既有 demo 已开 | 同上 | 营业状态与 `STORE_CLOSED` / `DAILY_RHYTHM_REQUIRED` |
| `CHIYO_WORLD_DELIVERY_TIME_ENABLED` | 视实例配置 | 决定 `delivery.mode` = `temporal_home_delivery`(7200s) 还是 `instant_home_delivery`(0s) | 「还在路上的东西」与布置按钮的先后 |
| `CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED` | **deny（默认不设）** | 操作者/演示进程显式设置；`world_body_config.owned_environment()` 只回显、不改写，GET 绝不自动开 | 房间灯面板：关着时 `light` 不是 `available`，界面显示「这个功能在当前实例里关着」且不给开关按钮 |
| substrate schema | v1.2 仍可读 | `migrate_substrate` **只在内存**升到 `world.body.substrate.v1.3`（新增 `fixtures` 块；缺条目 = power UNKNOWN） | 灯的 `power`：无条目时显示「未知」而不是「关着」；落盘只发生在真实 `TOGGLE_FIXTURE` 写入时 |
| `HERMES_HOME` | 既有实例根 | 不改 | 所有读取都走实例自己的文档；本轮没有为旧 root 做任何自动启用/迁移 |

界面侧的已知差异（已按实际实现消费，不需要 C 加平行字段）：房间三块在 `data.objects.*` 而不是顶层；
多盏灯时 `local_environment.light.fixture_ids` 只列「开着的那几盏」（power=off 时列全部在场灯），
所以**逐盏**状态改用 `actions.verbs[TOGGLE_FIXTURE].candidates[].power`，没有 candidates 时才退回聚合 power。
