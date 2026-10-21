# rc1 房间生活契约：实体商店 / 自有家具布置 / 房间灯

> 状态：**冻结（本轮 C 单写者产出）**。P（`components/phone/`）、U（`components/phone/web/`）按此消费，不再各自重判任何世界/经济规则。
> 独立于 `v04-phone-shopping-contract.md`、`v04-world-body-contract.md`（那两份是本轮之前的已验收契约，**不修改**）。
> 根：`D:\nyairo-rc1-body-backend`，HEAD `41e818f`（rc1）+ 已验收 phone/worldbody dirty 全部保留。
> 实现代码：`components/world/scripts/**`、`components/world/service/**`（窄改）；测试：`tests/test_store_room_owner.py`、`tests/test_room_environment.py`。

## 0. 三条不可让步的事实

1. **读永远是读。** GET 不配送、不物化、不布置、不发钱、不推进身体时间。到货只有 `WorldBodyService.tick()` 写；布置只有 `ARRANGE` 写；开关只有 `TOGGLE_FIXTURE` 写。
2. **灯不是猜出来的。** 只有「已声明的 fixture（catalog）+ 已记录的 power（substrate）+ 该 fixture 真的摆在当前 place/area」三者同时成立，才有 `light.status=available`。缺任何一条就是 `unknown` 并带 `reason_code`。**`unknown` 绝不是 `off`，也不是强度 0。**
3. **家具不是拿起来的。** `ARRANGE` 移动的是**自己的、已到货的**家具，`takeable` 一个字节都不变（床永远 `PICK_UP` 不了）。桌面/椅面/床面是**instance 级**的，两张桌子不会共享一块 `desk_surface`。

---

## 1. HTTP（P 负责接线，字段由本契约冻结）

### 1.1 `GET /api/phone/store`

实体店货架。实体店是**地点**：必须真的在 `home_goods_store`，且 `world_daily_rhythm` 判定的营业状态为 `OPEN`。

| 字段 | 类型 | 含义 |
|---|---|---|
| `store_id` | string | `"home_goods_store"` |
| `channel` | string | `"home_goods_store"`（与 `"phone_virtual_store"` 互斥，不可跨渠道） |
| `store_kind` | string | `"physical_counter"` |
| `availability` | object | `{status: "OPEN"|"CLOSED", reason_code, open_hour, close_hour, ...}`（世界自己的营业规则） |
| `location` | object | `{location_id, area_id}`：她此刻真实的 place/area |
| `at_store` | bool | 是否真的在该店 |
| `status` | string | `available` / `rejected` / `unavailable` / `disabled` |
| `reason_code` | string? | `STORE_LOCATION_REQUIRED` / `STORE_CLOSED` / `GENESIS_NOT_INITIALIZED` / `ECONOMY_STATE_ERROR` / `ECONOMY_CAPACITY_EXCEEDED` / `FEATURE_DISABLED` / `WORLD_FEATURE_REQUIRED` / `DAILY_RHYTHM_REQUIRED` / `WORLD_LOCATION_UNAVAILABLE` / `DAILY_RHYTHM_UNAVAILABLE` |
| `currency` | string | `"JPY"` |
| `balance` | int? | 当前余额（`null` 表示经济文档不可读/未初始化，**不是 0**） |
| `catalog[]` | array | 六件：`basic_bed 5800`、`simple_desk 4200`、`simple_chair 1800`、`bedside_lamp 900`、`small_bookshelf 3000`、`floor_cushion 700` |
| `catalog[].usable_kind` | string | 此渠道恒为 `FURNISH`（柜台只卖家具） |
| `catalog[].capability` | object | M15F 词表（`takeable/portable/placeable/fixed/hand_requirement/weight_class`），家具恒 `takeable=false` |
| `catalog[].declaration` | object | `{surfaces, relocatable, usable_kind}`；`surfaces` 是该家具提供的 **surface kind** |
| `catalog[].affordable` | bool? | 余额够不够（`null` = 余额不可知） |
| `purchase_available` | bool | 此刻**可以尝试**下单（在店 + 营业 + 容量未满）。**成功不等于到货** |
| `capacity_available` | bool | 经济 256 条上限是否还有空间 |
| `payment` | object | `{currency, source:"world_wallet", cash:false, top_up:false, outbound:false}` |
| `delivery` | object | `{destination_location:"home/bedroom", mode:"temporal_home_delivery"|"instant_home_delivery", duration_seconds:7200|0, arrival_written_by:"world_body_service.tick", placement_requires:"ARRANGE"}` |
| `pending_delivery_count` / `owned_furniture_count` | int | 真实计数 |
| `read_is_pure` / `paid_real_money` | bool | 恒 `true` / `false` |

### 1.2 `POST /api/phone/store/purchases`

请求体：`{"catalog_item_id": "simple_desk", "operation_key": "op-..."}`。**没有价格、没有余额、没有 uid/home/authority**（客户端给这些一律由 P 的既有白名单拒绝）。

成功（`status: "applied"` / 重放 `"already_applied"`）时 `data` 字段：

| 字段 | 含义 |
|---|---|
| `channel` | `"home_goods_store"` |
| `transaction_id` / `purchase_intent_id` | 一笔 DEBIT 与它的幂等键（= `operation_key`） |
| `catalog_item_id` / `item_instance_id` | 买的什么、哪一件 |
| `price` / `currency` / `current_balance` | 目录价（服务端决定）、币种、扣后余额 |
| `occurred_at` | 交易时刻 |
| `ownership` | `"OWNED"`（钱与产权是**一次**原子写入） |
| `location` | 该实例此刻的粗定位：`in_transit/home/bedroom`（在途）或 `home/bedroom`（即时到） |
| `delivery_location` / `delivery_provenance` | `home/bedroom` / `"store_counter_home_delivery"`（**销售渠道**证明；配送机制名仍是 `temporal_home_delivery`） |
| `delivery_status` | `PENDING` / `DELIVERED` |
| `expected_delivery_at` / `delivered_at` | ISO 或 `null`（配送时长 7200s 不变） |
| `materialization` | 恒 `"pending_arrange"`：到货 ≠ 摆好。真正的「落到房间」由 owner tick 完成（见 §1.4） |
| `placement_requires` | `"ARRANGE"` |
| `delivery_timeline_status` | `registered` / `unavailable`（时间线登记，不是到货） |

拒绝码（`ok:false`，`status:"rejected"`，**钱一分不动**）：
`STORE_LOCATION_REQUIRED`、`STORE_CLOSED`、`ITEM_NOT_SOLD_AT_STORE`（手机渠道商品/不存在）、`ITEM_NOT_ARRANGEABLE`、`INSUFFICIENT_FUNDS`、`ECONOMY_CAPACITY_EXCEEDED`、`UNKNOWN_CATALOG_ITEM`、`INVALID_STORE_PURCHASE_INPUT`、`OPERATION_KEY_CONFLICT`、`OPERATION_TARGET_CONFLICT`、`PURCHASE_LEDGER_INCONSISTENT`、`LOCAL_OWNER_*`。
`status:"unavailable"`：`ECONOMY_STATE_ERROR`、`GENESIS_NOT_INITIALIZED`、`WORLD_*`、`DAILY_RHYTHM_*`。`disabled`：`FEATURE_DISABLED`。


### 1.4 到货物化：唯一 writer 是 owner tick

下单成功**不**产生 placement。到货（`DELIVERED` 且 `current_location == "home/bedroom"`）之后，由
`WorldBodyService.tick()` 用**同一个**物化 seam 把实例落到合法的到货点：

* seam = `world_phone_shopping.materialize_owned_item()`（内部即既有 `materialize_purchase()`，与手机渠道共用，**没有第二份实现**）；
* 前置全部由 owner 真实派生：经济实例存在、`disposition == OWNED`、`current_location == "home/bedroom"`，
  且 `world_consumable_contract.purchase_voucher()` 能从真实的 `PURCHASE`/`DEBIT` 流水证明（**P 不得自己声明能力**）；
* 落点固定 `{"kind":"room","location_id":"home","area_id":"bedroom"}`（不写 hand，不写 surface）；
* 幂等：同目标 → `already_materialized`；已有**不同** placement → `PLACEMENT_CONFLICT` 且**绝不覆盖**；
  消费终态（有 receipt）→ `ALREADY_CONSUMED`，**绝不复活**；
* 因此时序是：**买 → （temporal: 到期 / instant: 立即）→ 下一个 owner tick 到货并落位 → ARRANGE 才合法**。
  到货与 tick 之间的 `NOT_MATERIALIZED` 是真实、短暂的中间态，不是死路。
* 读路径（GET/投影）永远不物化：`store_shop_projection`/`deliveries_projection`/`home_items_projection` 是纯读。

tick 的 `deliveries` 报告：`{"realized": {...}, "materialized": [{"item_instance_id","status","reason_code","placement"}]}`。

### 1.3 operation key 的唯一命名空间

一把 key 在整个手机里只有一个含义，跨渠道/跨动词都算冲突：

* 同 key + 同渠道 + 同商品 → `already_applied`（钱只扣一次）；
* 同 key + 柜台渠道 + 别的商品 → `OPERATION_TARGET_CONFLICT`；
* 同 key 已在**手机虚拟店**渠道用过 → 柜台 `OPERATION_KEY_CONFLICT`（反向亦然）；
* 同 key 已被某次 world 动词（MOVE/PLACE/ARRANGE/…）用过 → `GET/POST` 一律 `OPERATION_KEY_CONFLICT`（`world_phone_world` 已有执行号推导，P 不要另造）。

### 1.4 位置判定：一处 place 级只读（GET 与 POST 共用）

「她在不在柜台」只有**一个**判据，读法与 `GET` 完全同源：

```
_read_world_location(hermes_home=…)  ->  (place_id, area_id)      # 直读 data/world/world_state.json
_current_world_location(...)         ->  place_id | None          # world feature 关掉时 None
```

规则：

* **place 级**，不是节点级。判据只比 `place_id == "home_goods_store"`（`STORE_LOCATION_ID`）；`area_id`（`counter` / 门口 / 店里任意区）不参与，进店就算在柜台，API 再按营业时间判 `STORE_CLOSED`。
* **不参与 `world_id` 比较**。旧实现用 `world_living_skeleton.read_move_state` 读位置，它返回 `<place>/<area>` 这种导航节点、并且硬比 `economy.WORLD_ID`（写死 `shiomi_city`）；打包世界（如 `coastal_town_929711`）因此被读成「没有位置」→ `WORLD_LOCATION_UNAVAILABLE`，而 GET 因为一直走 `_read_world_location` 却是 `available`／`at_store=true`。两边曾经不是同一个函数，这就是那次现场 503 的唯一原因。
* **纯读**。这条路径不 realize 走路、不结算、不落盘；到店只能由玩家真的走到（timed travel 的 `expected_arrival_at` 过后由 owner 侧 realize），`GET` 永不把在途位置变成本地位置。
* 位置缺失（没有 `world_state.json`、没有 `location`）一律 `WORLD_LOCATION_UNAVAILABLE`（fail-closed），不伪造 position、不退回 `home`、不放宽到店门。

---

## 2. World 动词（走既有 `POST /api/phone/world/actions`，P 只加白名单）

`VERBS` / `GATEWAY_LOCAL_OWNER_VERBS` / `LOCAL_OWNER_ACTIONS` 三处现在都是：

```
("MOVE", "PICK_UP", "PLACE", "POSE", "REST", "ARRANGE", "TOGGLE_FIXTURE")
```

`CONSUME` 仍只属于购物的 `use` 通道（不变）。

### 2.1 `ARRANGE` — 摆放/重排**自己的**家具

```
params = {"object_id": "<item_instance_id>", "target": <placement_target>}
target 只接受：
  {"kind":"room",    "location_id":"home", "area_id":"bedroom"}
  {"kind":"surface", "location_id":"home", "surface_id":"desk_surface:<同一个 item_instance_id>"}
```

前置（全部 fail-closed，任一不成立即拒绝、零写入）：

1. 经济文档里存在该实例且 `disposition == OWNED`（`NOT_AN_ECONOMY_INSTANCE` / `NOT_OWNED`）；
2. 该 catalog 声明 `relocatable: true`（`NOT_RELOCATABLE`）；
3. **已到货**：`current_location == "home/bedroom"`（在途 → `NOT_DELIVERED`）；
4. 她此刻在 `home`（`NOT_HOME`），且该家具当前 placement 也在 `home`（`FURNITURE_NOT_HOME` / `NOT_MATERIALIZED` / `FURNITURE_IN_HAND`）；
5. 目标 `location_id` 只能是 `home`（跨 places → `NOT_CURRENT_AREA`）；
6. `room` 目标：世界包声明过 home 的 area 时必须落在其中（`UNKNOWN_DESTINATION` + `declared_areas`），没有声明 indoor area 的汐见市默认世界只允许**当前** area（`NOT_CURRENT_AREA`）；
7. `surface` 目标：`surface_id` 必须是 `<该家具声明的 surface kind>:<它自己的 instance_id>`（`SURFACE_NOT_DECLARED_FOR_ITEM` / `SURFACE_OWNER_MISMATCH`）；
8. 目标与当前 placement 完全相同 → `status:"noop"`、`reason_code:"already_arranged"`（零写入）。

成功 `data`：`status:"applied"`、`reason_code:"arrange_committed"`，并带

```
"arranged": {"object_id", "target", "carried": [子物件 id...], "surface_ids": ["desk_surface:<id>", ...]}
```

**子物件一致性**：已经放在该家具自己桌面/椅面/床面上的东西（`kind:"surface"` 且 `surface_id` 指向它）会在**同一次原子 substrate 写入**里跟着搬家，`carried` 列出它们；不存在「家具走了、东西还挂在空气面上」的中间态。

### 2.2 `TOGGLE_FIXTURE` — 灯的**绝对**开关

```
params = {"object_id": "<item_instance_id>", "state": "on" | "off"}
```

* `state` 必填且绝对：**没有** blind toggle（重复同一 state → `noop`，`reason_code` = `fixture_already_on` / `fixture_already_off`，revision 不变、无 committed 账本）；
* 只接受**已声明**的可通电 fixture（当前 = `bedside_lamp`）：否则 `NOT_A_FIXTURE`；
* 灯必须真的摆在当前 place 且（有 area 时）当前 area：否则 `FIXTURE_NOT_HERE`；
* 成功 `data`：`status:"applied"`、`reason_code:"fixture_committed"`、`world_mutation_refs:["world.fixture=<id>:on"]`，并带

```
"fixture": {"object_id", "state", "previous_state", "since", "declared_source", "band"}
```

* 未知/未声明对象保持 `unknown`，**绝不**当作 `off`。

---

## 3. 快照字段（`world_phone_world.snapshot()`；九大块形状不变，新增字段挂在既有块里）

顶层仍是九块：`world / location / local_environment / destinations / travel / objects / body / actions / recent_executions`。
（理由：`tests/test_phone_world_api.py` 与 U 的既有渲染都以「九块」为契约，新增内容一律做**块内附加字段**。）

### 3.1 `local_environment.light`

```jsonc
"light": {
  "status": "available" | "unknown",
  "power": "on" | "off" | null,
  "band": "dim" | "moderate" | "bright" | null,
  "band_source": "environment.light.v1",
  "source": "world_room_environment",
  "declared_source": "catalog:bedside_lamp" | null,
  "since": "<ISO>" | null,
  "on_seconds": <int> | null,
  "fixture_ids": ["<item_instance_id>", ...],
  "reason_code": null | "NO_DECLARED_LIGHT_IN_AREA" | "FIXTURE_POWER_UNKNOWN" | "NO_DECLARED_FIXTURE_PLACED_IN_AREA" | "SUBSTRATE_UNAVAILABLE" | "ROOM_ENVIRONMENT_UNAVAILABLE",
  "place_id": "home", "area_id": "bedroom",
  "light_source_is_declaration": true,
  "temperature_or_noise_inferred": false
}
```

U 的渲染规则：`status != "available"` 就显示「服务没有提供房间光状态」并给出 `reason_code` 的人话，**不要**显示 0、不要显示「黑暗」。`available + power="off"` 才是真正的「关着」。

### 3.2 `objects.surfaces`

```jsonc
"surfaces": {
  "status": "available" | "unavailable",
  "source": "world_body_substrate",
  "here": [{
     "surface_id": "desk_surface:<item_instance_id>",
     "surface_kind": "desk_surface" | "chair_surface" | "bed_surface",
     "label": "desk",
     "owner_object_id": "<item_instance_id>",
     "owner_catalog_item_id": "simple_desk",
     "location_id": "home", "area_id": "bedroom",
     "pose_condition": "seated" | "lying" | null,
     "rest_modes": ["rest", "sleep_like"],
     "items": ["<子物件 id>"]
  }],
  "count": 1,
  "rest_here": [...],           // rest_modes 含 sleep_like 的那些
  "declared_kinds": ["bed_surface","chair_surface","desk_surface"],
  "instance_scoped": true,
  "read_is_pure": true
}
```

`desk_surface` / `chair_surface` **没有**裸 id：只有 `<kind>:<instance>`。`bed_surface` 额外保留裸 id（M13A.1 bootstrap 写过它，既有实例不必重置）。

### 3.3 `objects.owned_furniture`

```jsonc
"owned_furniture": {
  "status": "available" | "unavailable",
  "source": "world_property_economy",
  "items": [{
    "item_instance_id", "label", "catalog_item_id", "name",
    "usable_kind": "FURNISH" | "CARRY",
    "relocatable": true,
    "surfaces": ["desk_surface:<id>"],
    "current_location": "home/bedroom" | "in_transit/home/bedroom",
    "delivered": true,
    // 到货后到「下一个 owner tick」之间是 AT_HOME_UNPLACED + placement=null（真实短暂中间态）；
    // tick 落位成功后就是 DELIVERED + 一个 room placement（见 §1.4）
    "delivery_status": "DELIVERED" | "IN_TRANSIT" | "AT_HOME_UNPLACED",
    "placement": {"kind":"room","location_id":"home","area_id":"bedroom"} | null,
    "arrange_available": true
  }],
  "count": 2,
  "arrange_available": true,
  "unplaced_delivered": ["<已到货但还没布置的 id>"],
  "read_is_pure": true
}
```

### 3.4 `objects.deliveries`

```jsonc
"deliveries": {
  "status": "available",
  "mode": "temporal_home_delivery" | "instant_home_delivery",
  "duration_seconds": 7200 | 0,
  "destination_location": "home/bedroom",
  "pending": [<delivery projection>], "delivered": [...],
  "pending_count": 1, "delivered_count": 0,
  "realized_by": "world_body_service.tick",
  "read_delivers_items": false,
  "items": [...]
}
```

`delivery projection` 字段：`delivery_id`、`purchase_intent_id`、`item_instance_id`、`destination_location`、`purchased_at`、`expected_delivery_at`、`delivered_at`、`status`、`delivery_provenance`。

### 3.5 `actions`

`actions.local_owner_verbs` 现在是**七个**（见 §2）。`actions.verbs[]` 里会出现 `ARRANGE`（`candidates` = 可布置家具 + `rooms` = 可布置 area）与 `TOGGLE_FIXTURE`（`candidates` = 当前 area 的已声明 fixture，含 `power`/`since`/`band`），并各自带 `blocked_reason`。

---

## 4. Gateway（本地可信 socket，P 不需要直连）

* 读：`get_snapshot`、`get_observation`、`get_body_signals`、`get_available_actions`、`get_action_result`、`get_status`、**`get_environment`**、`ping`。
* `get_environment` 返回 `{kind, schema_version, source:"world_room_environment", location, light, surfaces, fixture_block_present, environment_channel_enabled, body_input_model:"environment.light.v1", read_is_pure:true}`。
* 写：`submit_action`、`submit_local_owner_action`（动词白名单已含 `ARRANGE`/`TOGGLE_FIXTURE`）；结果透传 `fixture`、`arranged`、`rest_surface_id`。

## 5. 经济 / substrate owner API（C 侧，供 P 的 facade 与测试使用）

```python
world_property_economy.store_shop_projection(*, hermes_home, environment, now) -> dict     # 纯读
world_property_economy.store_purchase_item(*, catalog_item_id, operation_key, local_owner,
                                           hermes_home, environment, now) -> dict          # 命令
world_property_economy.realize_due_deliveries(*, hermes_home, environment, now) -> dict     # 只由 tick 调
world_property_economy.deliveries_projection(*, hermes_home, environment) -> dict           # 纯读
world_property_economy.arrange_verdict(*, item_instance_id, target, placements,
                                       world_location, hermes_home, environment) -> dict    # 纯判据
world_property_economy.catalog_declaration(catalog_item_id) -> dict                         # {surfaces, relocatable, usable_kind, light_band?}
world_property_economy.declared_home_areas(environment) -> set[str]

world_body_substrate.surface_instance_id(kind, object_id) -> "kind:object"
world_body_substrate.surface_declared / surface_kind_of / surface_owner_instance / surface_owner_catalog
world_body_substrate.surfaces_here(state, catalog_by_instance, *, location_id, area_id) -> list
world_body_substrate.rest_surfaces_here(state, catalog_by_instance, *, location_id, mode)
world_body_substrate.fixture_of(state, object_id) -> dict|None
world_body_substrate.set_fixture(state, object_id, power, *, since, declared_source, expected_revision)

world_room_environment.light_for_home(hermes_home, *, now, location) -> dict               # 纯读
world_room_environment.derive_light(*, placements, fixtures, catalog_by_instance,
                                    location_id, area_id, now) -> dict
world_room_environment.environment_input(light) -> {"class":"ENVIRONMENT","sensory_intensity":x}|None
```

facade 侧（`world_phone_shopping.PhoneShopping`）新增：`store()`（= §1.1）、`store_purchase(catalog_item_id, operation_key)`（= §1.2）。既有的 `wallet()/shop()/inventory()/purchase()/use()/operation()` **行为不变**。

## 6. 存储层（最小兼容扩展，不新建 store）

* `data/world/body_substrate.json`：schema **`world.body.substrate.v1.3`**。唯一新增块：
  ```jsonc
  "fixtures": {"<item_instance_id>": {"power":"on"|"off","since":"<ISO>","declared_source":"catalog:bedside_lamp","revision":<int>}}
  ```
  缺条目 = power **UNKNOWN**（不是 off）。v1→v1.2→v1.3 迁移只在内存里发生（`migrate_substrate`），落盘只在真实写动作时；placement/hands/receipts 结构一字不改。
* 经济文档 `state/world_property_economy.json`：**结构不变**，只多一组合法 provenance `("local_owner_store_purchase", "store_counter_home_delivery")`；配送记录本身仍用 `temporal_home_delivery`。
* 房间灯状态**没有**第二个世界 store；surface **没有**注册表文件（全部由 placement + 经济 catalog 派生）。

## 7. Body 通路（数学/阈值零改动）

* 唯一输入：`{"class":"ENVIRONMENT","sensory_intensity": LIGHT_BAND_INTENSITY[band]}`，`environment.light.v1 = {dim:0.20, moderate:0.45, bright:0.70}`。
* **永不**产出 `temperature_tendency`；`sensory_load`/`warmth` 的速率、阈值、`POSE_STRAIN`、恢复门槛、六指标与信号波段全部未改。
* `dim` 灯不触发 `sensory_load_elevated`（0.60）是**正确结果**，不是缺陷，禁止为了页面好看调亮或改速率。
* **观察锚点**：`anchor = max(body.last_advanced_at, light.since)`。灯是 30 秒前才开的，就只谈这 30 秒——绝不把之前的停机时段算成「灯一直亮着」；也正因如此，旧 root 现在开灯不会被历史 gap 永久卡住。
* tick 规则：一个 tick **至多一次** Body 时间推进（恢复推进占了，光就等下一个 tick）；`anchor` 起算 < 60s 不动。
* **停机 gap 语义**（`anchor` 起算 > 900s）：那一段**不喂任何环境输入**（`input = null`，断言里 `environment_gap_unknown = true`），但会用**一个有界窗口（≤900s）的无输入推进**把 Body 时钟推到 now，于是下一 tick 是正常窗口，而不是被同一个 gap 永久拒绝。sensory_load 只按既有「无刺激」规则变化，不朝灯档位上升，也**不补造整夜持续观察**。
* 开关 flag：`CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED`，**默认 deny**。既有实例如何启用：操作者/演示进程显式设置该环境变量（`world_body_config.owned_environment()` 只回显、不改写），**不得**由 GET 自改、也不得为旧 root 自动开启。

## 8. 明确不做

容器（抽屉/书架内部）、书籍内容、手持家具、通用 USE、3D/天气/温度/噪音、自动作息与自动关灯、第二钱包/第二 inventory/第二 body 库、给灯加「亮了睡不着」之类新门槛、为验收加速路线/营业/配送。
