# Agent Note: rc1 房间/实体日用店界面——货架购买、自有家具布置、instance 台面与真实灯开关

Status: implemented

## Problem

上一轮世界屏能看位置、街区、手边物件、姿势与恢复，但生活里最具体的三块还缺着：

- **实体日用店只是地图上的一行字**。`home_goods_store` 在拓扑和营业表里都有，界面却既看不到货架，
  也说不出「店开着没、人在不在店里、余额够不够、买完怎么到手」。手机线上店与街上那家店的授权、
  位置与交付规则完全不同，混在一屏就成了「一屏两个渠道、谁也不是谁」。
- **买来的家具没有地方摆，也看不出到没到**。`OWNED` 不等于到货、到货不等于能用；界面少说一句
  「还没到货」，用户就会以为已经能坐了。
- **房间的灯是这轮最容易被造假的字段**：`local_environment.light` 没人消费时，屏幕上任何
  「灯亮着 / 房间是暗的 / 亮度 0.2」都是自己编的；`13-产品界面` 要求物件必须回答「怎样操作、最后实际
  改变了什么」，`13:82` 明令不许「先说拥有、后补交易」。

同时两个已修好的真实缺陷不能回退：`handSlots`/`heldObjects` 的**协议原值 vs 展示标签分离**
（69 字 `item-<64hex>` 曾被遮罩串顶替成 PLACE 的 payload），以及购买/使用后的
`invalidateWorldSnapshot()` 跨 app 缓存失效。

## Decision

只改 `components/phone/web/{app.js,style.css}`（U 写集）。开工时 `rc1-room-living-contract.md` 还不在树里，
所以先把界面结构与适配表写好；C/P 的代码落进来后逐字段对齐到真实实现，不猜、不按名字判断能力：

1. **两个渠道分开**：`shop` 屏顶部固定 `channel-card`（「渠道：手机线上店」+「去实体日用店」），
   新屏 `store`/`store-item` 走 `GET /api/phone/store` 与 `POST /api/phone/store/purchases{catalog_item_id,
   operation_key}`；购物的 scope 是 `store-purchase:<catalog_item_id>`，与手机线上店的 `purchase:` 不同。
   主屏只加两个行内按钮（实体日用店 / 房间与布置），**不加第九个 tile**，`HOME_ORDER` 与旧检查一字不差。
2. **动词名只认 owner 报的**：`ROOM_VERB_NAMES={arrange:['ARRANGE','RELOCATE'], fixture:['TOGGLE_FIXTURE']}`；
   `roomVerbFor()` 只用 `data.actions.verbs` 里真出现的那个名字（C 现在报 `ARRANGE`），没有就不给按钮。
   旧 `WORLD_VERBS` 五个字面量原样保留，新增三个只进 `ALL_WORLD_VERBS`。
3. **参数键 fail-closed 且与 C 的 PARAM_SPEC 一致**：`ARRANGE_PARAM_KEYS=['object_id','target']`、
   `FIXTURE_PARAM_KEYS=['object_id','state']`（`world_phone_world.PARAM_SPEC` 与 P 的白名单都要 `object_id`）。
   `paramKeyMismatch(reported, planned)` 要求 owner 报的键集**包含**界面要发的每个键，少一个就禁用并说明；
   `item_instance_id`/`fixture_id` 只作输入别名，payload 一律输出 `object_id`。
4. **摆放/布置目标只用已冻结形状**（`components/phone/server.py` `WORLD_TARGET_FIELDS`）：
   `room={kind,location_id,area_id}`、`surface={kind,location_id,surface_id}`；`buildArrangeTarget()` 只收
   room/surface，container/hand 拒；PLACE 走「房间优先、其次台面」。
5. **房间三块在 `objects` 里，台面是 instance 级的**：契约 §3.2–3.4 把 `surfaces`/`owned_furniture`/
   `deliveries` 放在 `objects` 块的块内附加字段（九大顶层块形状不变），界面就只从 `objects` 读，
   顶层同名键仅作旧快照兜底，没有要求 C 加平行顶层字段。`world_body_substrate.SURFACE_KINDS` 的
   `desk_surface`/`chair_surface` 只以 `<kind>:<owner item_instance_id>` 存在（`bed_surface` 兼容裸 id），
   界面原样回传 `surface_id`（例如 82 字 `desk_surface:item-<64hex>`），两张桌子不会串；
   `pose_condition`/`rest_modes`/`items` 只翻译不推断——没有声明就写「服务没有声明这张台面能做什么姿势」。
6. **灯是绝对状态，逐盏报**：只发 `{object_id, state:'on'|'off'}`，`toggle` 被 payload 层拒；
   `light.power` 是房间聚合值（on 时 `fixture_ids` 只列开着的那几盏，off 时列全部在场灯），
   所以每盏的开关状态优先读 `actions.verbs[TOGGLE_FIXTURE].candidates[].power`（owner 逐盏报），
   没有 candidates 才退回聚合 power；`power` 缺失时两个按钮都可用但写明是绝对状态；
   `light.status!=='available'` 时没有任何开关按钮，`unknown` 文案「房间光未知：… 未知不等于『黑的』」；
   回执只复述 `data.fixture.{status,state}` 并固定附「灯变了不等于身体变了」。
7. **到货 / 布置 / 可用分三层读**：家具卡先看 `delivered`/`delivery_status`（`IN_TRANSIT` →
   「还没到货，不能提前布置」），再看 `relocatable`、`arrange_available`，最后才看世界动词可用性；
   布置弹层写「摆好不等于现在能用」；回执读契约的 `data.arranged{target,carried,surface_ids}`
   （`carried` 是被一起搬走的子物件，`reason_code=already_arranged` 时明说没有重复写入）；
   `materialization=pending_arrange` 的购物流水也照实转述。
8. **跨 app 失效，不重复 fetch**：`refreshAfter()` 新增 `store-purchase` 分支（清 store/shop/inventory/wallet
   + `invalidateWorldSnapshot()`，只在当前屏重读一次）；手机购买与世界动作只**作废** `store` 缓存，
   不发请求。房间四块全在 `/world` 快照里，进世界屏不多发任何请求（测试对 GET 计数做了断言）。
9. **窄屏与长标识**：新类全部 `min-width:0` + `overflow-wrap:anywhere`，`@media (max-width:430px)` 里
   灯开关改纵向整行；协议原值只进 payload，屏幕一律 `safeLabel()`。

## Alternatives considered

- **主屏加第九个「实体日用店」tile** — 立论：入口最短。否决：旧 UI 检查断言主屏 8 个 tile，而实体店是
  「商店」这条链的另一个渠道，不是新 app；放进商店屏的渠道卡里才不造第二个总入口。
- **把布置动词写死成一个名字** — 立论：少一层间接。否决：定名权在 C，写死后 C 用另一个名字就 409
  `NOT_EXPOSED`、看起来像后端坏了。现在按 owner 报的名字发，两个都没报就不给按钮。
- **自造全局 `desk_surface`** — 立论：先让「放桌上」能点。否决：两张桌子共用一块 id 就是串物；substrate
  的 instance 级 id 是唯一真相，界面只回传它。
- **unknown 光画成「暗」并给个 0.2 亮度条** — 立论：界面好看。否决：未知不是 0，也不是黑
  （`env-explorer-report.md` 与验收文档都写「不加无权威输入」）。
- **开灯后顺手抬一格 `sensory_load`** — 立论：让后果立刻可见。否决：界面不产身体事实；测试里有负控断言
  世界屏不出现「身体已经」。
- **前端定时器假装到货** — 立论：不用等 2 小时。否决：到货由世界时间线推进，界面按时钟「送到」就是造假。
- **把 `store` 塞进 `/shop` 共用一个响应** — 立论：少一个端点。否决：两个渠道的授权/位置/营业约束不同，
  合并后界面必然开始猜「这条目录项属于哪个渠道」。

## Consequences

- **收益**：界面现在有一条能走通的真实链——主屏 →实体日用店（营业/余额/交付/待收货件数一屏说清）→ 商品 →
  确认购买（原字节目录 id、独立 scope）→ 回世界看到「还没到货」→ 到货后可「布置：放进现在的房间」→
  台面上放回手里的小物件（每块台面自己的 id）→ 房间灯 `on/off` 绝对状态。所有可用性、档位、秒数、
  原因码都来自快照，排错信息在折叠的 `tech-details` 里。
- **诚实性代价**：快照里没有的字段一律显示「服务没有提供 …」而不是补一个看起来合理的值；`unknown` 光
  没有开关按钮。这比给一个假按钮难读，但不会让人以为功能已经通了。
- **测试形态**：新增 `tests/test_room_living_ui.mjs` 21 条（真实 `node --check`、纯函数字段搬运、stub DOM
  点击链：货架只读、购买→回世界、pending 无布置按钮、两张台面不串、灯 on/off/unknown、unknown 键查询、
  390 静态样式）。是 stub DOM，**不**证明浏览器布局/焦点环；真实页面串链留给父级。
  旧 `test_phone_world_ui.mjs`（25）与 `test_phone_ui.mjs`（34）在本轮改动后仍全绿（三个文件合计 80）。
- **不写数据**：界面不初始化、不发钱、不物化、不推进 tick、不落盘；`GET /store` 与 `GET /world` 纯读，
  只有用户点确认的 `POST /store/purchases` 与 `POST /world/actions` 才产生写入。
- **已知缺口**：`actions.verbs[].params` 由 gateway 上报，若 C/P 的网关键集变化，界面会 fail-closed 禁用
  并说明差异（不会改名硬发）；图形化房间布置不做（契约没有坐标）；容器目标、书籍内容、新身体门槛都不做。
  本轮没有在真实实例/真实浏览器上跑过这条链（未连 live 5651、未重买、未重置、未重启），
  也没有真模型/支付/生产/commit。
