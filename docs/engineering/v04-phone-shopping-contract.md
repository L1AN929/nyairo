# v0.4 phone shopping —— 唯一实际契约（W 写集）

> 本文是 v0.4「虚拟手机购物」World 侧的**唯一契约来源**。P（phone server/demo）
> 只按本文接线，不另行定义字段；U（前端）只消费 P 暴露的 `/api/phone/*`。
> 状态：implemented（代码与本文同一提交事实）。

## 0. 边界

| 关注点 | owner | 本轮是否新增 store |
|---|---|---|
| 余额 / 流水 / 产权（钱与物的账） | `world_property_economy`（**唯一**） | 否 |
| 物件物理位置（placement） | `world_body_substrate`（**唯一**） | 否 |
| 消耗真相（receipt） | `world_body_substrate.consumptions`（**唯一**） | 否 |
| 动作提交 / 幂等 / 归因 | `world_action_resolver` + `world_execution_ledger` | 否 |
| 一次性提交授权 | `world_action_authorization` registry | 否 |
| 手机渠道与物化编排 | 新模块 `components/world/scripts/world_phone_shopping.py` | 否（只读+编排） |

**没有新经济 store，没有第二份产权真相。** 购物与物化跨两个 store（economy /
substrate），不假装全域 ACID：钱+产权是经济 owner 的单次原子保存；物化是
substrate owner 的单次原子保存，可幂等重放并事后补完。

## 1. 手机渠道 = 显式新增虚拟线上店

* 店 ID：`phone_virtual_store`（`CHANNEL_PHONE_VIRTUAL_STORE`）。
* 明确声明：这是**虚拟线上店**，`availability = {"status": "OPEN",
  "scheme": "virtual_store_24h"}` —— 24 小时营业是它自己的声明，不是绕过物理店规则。
* 物理店 `home_goods_store` 的 `_store_check`（在店位置 + `world_daily_rhythm`
  营业时间）**一字未改**；`shop_projection()` 行为不变，只是不再列出仅手机渠道的商品。
* 目录商品按 `channels` 归属：
  - 原家居目录 6 件（`basic_bed` … `floor_cushion`）→ `home_goods_store`；
  - 新增 `virtual_snack_wafer`（虚拟小点心，240 JPY，手持可携带、**非营养**）→
    `phone_virtual_store`。
* 不在本渠道的商品被购买 → `ITEM_NOT_SOLD_IN_PHONE_STORE`；在手机渠道买物理店商品
  同样被拒。没有现实支付、没有充值、没有外发、没有平台账号。

## 2. 本地 owner 身份（不可由客户端伪造）

`PhoneShopping` 由 P 的服务器进程构造，构造参数：

```python
PhoneShopping(
    hermes_home,                 # 绝对路径，必须 == 服务端 world_home
    environment,                 # Mapping[str, str]（含 feature 开关）
    *,
    local_owner_uid,             # 必须是真实 os.getuid()
    subject="chiyo",             # 只接受 world_property_economy.OWNER
    gateway_socket=None,         # <world_home>/run/gateway.sock 或显式路径
)
```

* 构造时即校验：`os.getuid() == local_owner_uid`、`subject == "chiyo"`、
  `hermes_home` 绝对且是已存在目录。任一不符 → 所有方法返回
  `rejected / LOCAL_OWNER_*`，不读不发钱。
* 请求 body 里的 `uid` / `authority` / `main_self_verified` / `home` / 价格 /
  余额一律**不作身份**：HTTP 层（P）负责丢弃；World 侧只认构造参数与
  `world_home`。经济 owner 另有一层 `validate_local_owner()`：`source` 必须是
  `local_owner_phone`、`uid` 必须等于本进程 `os.getuid()`、`subject` 必须是
  `chiyo`、`home` 必须等于经济状态所在目录。
* 旧 main-self 购买 API（`purchase_item(..., main_self_verified=True)`）行为
  **完全保留**；手机渠道走独立的 `phone_purchase_item()`，绝不把本地 owner 操作
  标成 `verified_main_self`（流水 provenance 写
  `source = "local_owner_phone_purchase"`、`delivery = "phone_virtual_store_home_delivery"`）。

## 3. 信封（所有 `PhoneShopping` 方法）

```json
{"ok": true|false, "status": "...", "reason_code": "...", "data": {...}}
```

`status` 取值：`applied` / `already_applied` / `rejected` / `unknown` /
`unavailable` / `disabled` / `not_found`。HTTP 层照抄（`{"ok","status","reason_code","data"}`）。

### 3.1 `wallet()` → `GET /api/phone/wallet`

`data = {currency, balance, transactions:[{transaction_id, kind, direction, amount,
occurred_at, catalog_item_id?, purchase_intent_id?}], owner, starting_grant}`
直接投影 `world_property_economy.wallet_projection()`（余额字段后端统一）。
`status=disabled` 当 feature 关闭；`unavailable/GENESIS_NOT_INITIALIZED` 当没有经济
genesis。**读不初始化、不发钱。**

### 3.2 `shop()` → `GET /api/phone/shop`

`data = {store_id:"phone_virtual_store", channel:"phone_virtual_store",
availability:{status:"OPEN",scheme:"virtual_store_24h"}, currency,
catalog:[{catalog_item_id,name,description,price,currency,usable_kind:"CONSUME"}],
purchase_available:bool, reason_code?, delivery:{destination:"home/bedroom",
mode:"instant_ownership_then_materialize"}, payment:{currency,cash:false,...}}`

`purchase_available=false` 时带 `reason_code`（`GENESIS_NOT_INITIALIZED` /
`ECONOMY_STATE_ERROR` / `FEATURE_DISABLED`）。浏览不需要在物理店内。

### 3.3 `inventory()` → `GET /api/phone/inventory`

`data = {items:[{item_instance_id, name, catalog_item_id, ownership, location,
delivery_status, usable, blocked_reason?, materialization, consumed, receipt_id?}],
pending_count}`

* `ownership` = 经济 `disposition`（`OWNED` / `GIFTED_AWAY` / `DISCARDED`）；经济记录在
  消耗后仍是 `OWNED`（产权保留），"耗尽"只由 substrate 回执证明；
* `location` = substrate 里的真实 placement（`room@home/bedroom` / `hand:right_hand`
  / `in_transit/...`）；没有 placement 时，尚未消耗 → 给经济记录位置并标
  `materialization="pending"`，**已消耗 → `location="consumed"`**（没有当前位置）；
* `materialization` 三个态 + 一个终态：`materialized` / `already_materialized`（内部
  重放用）/ `pending` / **`consumed`（终态）**。`consumed` 不是 `pending`：已经吃掉的
  东西没有"待物化"，也不会再被物化；
* `delivery_status`：`DELIVERED`（经济已交付且已物化）/ `PENDING_MATERIALIZATION`
  （钱已扣、产权已落、物化未完成）/ `IN_TRANSIT`（时间配送中）/ `CONSUMED`；
* `pending_count` **只数 `pending`**：已消耗的条目永不计入；
* `usable` = 该物**现在**能否经 Resolver 消耗（有购得凭证 + OWNED + 当前可达 +
  世界声明 + 未消费）；`blocked_reason` 给出第一条不满足的原因
  （`NOT_MATERIALIZED` / `NOT_HELD_OR_HERE` / `NOT_DECLARED` / `ALREADY_CONSUMED` /
  `NOT_OWNED` / `BODY_POSE_CANNOT_CONSUME` / `WORLD_SERVICE_UNAVAILABLE`）。

### 3.4 `purchase(catalog_item_id, operation_key)` → `POST /api/phone/purchases`

* `operation_key` 是幂等键，直接作为经济的 `purchase_intent_id`（唯一经济真相里的
  唯一幂等键）。同 key 重放 → `already_applied`，**不二次扣钱**；同 key 换商品 →
  `rejected / PHONE_PURCHASE_OPERATION_CONFLICT`。
* 原子：一笔 `PURCHASE` 流水 + 一个 `OWNED` item 在同一次 `_save()`（tmp+fsync+replace）。
* `data = {transaction_id, item_instance_id, catalog_item_id, price, currency,
  current_balance, occurrence_at, ownership, delivery_location, materialization,
  materialization_reason_code?}`。
* 扣款后物化失败：`status="applied"` 但 `data.materialization="pending"` 且带
  `materialization_reason_code`；**不二次扣钱**。用同一 `operation_key` 再调一次
  （返回 `already_applied`）即完成 recovery 物化。
* **消耗后重放同一个 key**：返回原购买 `already_applied`，但 `data` 是**终态**：
  `materialization="consumed"`、`consumed=true`、`delivery_status="CONSUMED"`、
  `location="consumed"`、`receipt_id`（原回执），且**没有** `placement` /
  `materialization_reason_code`。不重新 `place_object`、不写世界声明、不产生第二条回执、
  不二次扣钱 —— 已耗尽的东西不会被购买重放"复活"。
* 容量：`MAX_TRANSACTIONS=256` / `MAX_ITEMS=256` 到顶 → `rejected /
  ECONOMY_CAPACITY_EXCEEDED`，**不清理旧记录**（保留既有 256 条上限语义）。
* 余额不足 → `rejected / INSUFFICIENT_FUNDS`（`data.current_balance/price`）；
  `main_self_verified` 完全不参与手机路径。

### 3.5 `use(item_instance_id, operation_key)` → `POST /api/phone/items/use`

* 只经**原 Resolver**（`WorldBodyService` → `world_action_resolver`），手机侧不做
  任何"直接改 state"。执行 ID 由 operation key 稳定派生：
  `execution_id = "phone-exec:" + sha256("chiyo|phone_use|" + operation_key)[:32]`，
  同一个 key 永远是同一个 execution（重放安全，不会二次消耗）。
* **一个 key 只命名一个操作、一个目标**：
  - 该 key 已在经济里是购买（`purchase_intent_id`）→ `use()` 立即
    `rejected / OPERATION_KEY_CONFLICT`（`data.operation="purchase"`）；
  - 该 key 派生的 execution 已在执行账本里出现 → `purchase()` 立即
    `rejected / OPERATION_KEY_CONFLICT`（`data.operation="use"`）；
  - 同一 execution 的回执若属于**另一个** instance → `rejected /
    OPERATION_TARGET_CONFLICT`（带 `object_id` 与 `requested_object_id`），
    **绝不**把"这个 key 已经吃过别的东西"当成新目标的 `already_applied`。
* 传输：必须有**可信本地端口**（unix socket `<world_home>/run/gateway.sock`，
  0600、`SO_PEERCRED` uid==服务 uid）。没有 socket → `unavailable /
  WORLD_BODY_SERVICE_UNAVAILABLE`；**不自动启服务**（启动由 P 的 demo 负责）。
* 提交前由手机侧（可信进程）读一次 gateway `get_snapshot` 得到
  `observed_dependencies = {world.revision, world.location, body.pose}`；
  一次性授权由 `world_action_authorization.AuthorizationRegistry`（run_dir 下同一
  registry，无新 store）签发，服务端 `submit_local_owner_action` 原子 verify+consume。
* 服务端入口（新增窄命令 `submit_local_owner_action`）：
  - 只有服务端显式绑定 `CHIYO_WORLD_LOCAL_OWNER_PHONE=<subject>` 才启用，否则
    `rejected / LOCAL_OWNER_PHONE_NOT_BOUND`；
  - 请求里带 `authority` / `confirm_apply` / `main_self_verified` / `uid` /
    `binding` / `source` → `invalid_request`（权限只能由服务端盖章）；
  - 动词白名单：`CONSUME`（`LOCAL_OWNER_VERB_NOT_ALLOWED` 拒其它）；
  - 盖章 `authority = "local_owner_phone"`（**不是** `verified_main_self_tool`），
    Resolver 只对 `CONSUME` 接受这个 authority；
  - 响应带 `local_owner = {source:"local_owner_phone", uid:<服务 uid>,
    binding:{subject, world_home}}`；Resolver 把同样的 context 记进 committed
    ledger 的 `cross_authority_refs` 与结果 `local_owner` 字段。
* 消耗判定（`world_consumable_contract.consumption_verdict`）按顺序：
  1. 有 live placement（`NO_PLACEMENT`）；
  2. placement 是 `hand`/`room`/`surface` 且**手持或就在当前地点**
     （`NOT_HELD_OR_HERE`）；
  3. 经济事实可证（`ECONOMY_FACTS_UNAVAILABLE`，坏账本绝不当空）；
  4. 世界声明 `consumable=true`（`NOT_DECLARED`/`NOT_CONSUMABLE`）；
  5. `free_to_consume=true` → 旧免费路径；否则要求声明
     `consumption_permit="owned_purchase"`（`NOT_FREE`）；
  6. 走 permit 时必须拿出**owner 购得凭证**：item 在经济里 `acquisition_kind=PURCHASE`、
     `disposition=OWNED`、且有对应 `PURCHASE/DEBIT` 流水（同 `purchase_intent_id`、
     同 `catalog_item_id`、同 `transaction_id`），否则 `PURCHASE_VOUCHER_MISSING` /
     `PURCHASE_NOT_OWNED`；
  7. 其它经济物件（没有 permit）仍然 `ECONOMY_OBJECT` 拒绝 —— 旧规则不变；
  8. 姿态可操作（`POSE_CANNOT_CONSUME`）；place 供给的还要 OPEN
     （`LOCATION_CLOSED`/`RHYTHM_UNAVAILABLE`）。
  5→6 通过时 receipt `source = "purchased_item"`（第三种合法来源）。
* 消耗只发生一次：substrate `consumptions[execution_id]` 是唯一真相；重放同 key →
  `already_applied`，换 key 再消耗 → `rejected / ALREADY_CONSUMED`。
* 回执目标校验先于重放判定：只有"同 key + 同 instance"才是 `already_applied`。
* 经济记录**保留原产权**（item 仍 `OWNED`）；"耗尽"只由 substrate receipt 证明。
* Body 只走既有 `CONSUME` 轻操作后果（2s 操作量级），**不回血、不加营养/饥饿/健康**。

### 3.6 `operation(operation_key)` → `GET /api/phone/operations/<key>`

无新 store，全部派生：

1. 经济里存在 `purchase_intent_id == operation_key` 的流水 → 购买结果（`applied`，
   带 `transaction_id` / `item_instance_id` / `current_balance` / `materialization`）；
2. 否则 `world_execution_ledger` 里存在该 key 派生的 execution → `reconcile()`
   的 `SUCCESS`/`FAILURE`/`UNKNOWN`（`UNKNOWN` 原样返回，**不自动换 key 重发**）；
3. 都没有 → `ok=false, status="not_found", reason_code="OPERATION_NOT_FOUND"`。

**GET 只读**：本接口只读三个 owner（经济流水、substrate placement/receipts）、
不调用任何物化、不写 substrate、不建目录。缺物化时返回
`materialization="pending"`、`materialization_reason_code="NOT_MATERIALIZED"`、
`recovery="POST /api/phone/purchases with the same operation_key"`（一句事实说明，
不是让 P 在 GET 里补写）。`consumed` / `receipt` 来自 substrate 回执。

## 4. 物化（World owner materialization）

* 由 `world_phone_shopping.materialize_purchase()` 完成：读经济（权威），校验
  item `OWNED` + `current_location == "home/bedroom"` + 购得凭证，然后用
  `world_body_substrate.place_object()` 把 `{kind:"room", location_id:"home",
  area_id:"bedroom"}` 写进 substrate，`SubstrateStore.save()` 一次原子落盘
  （带 `expected_revision`）。
* 幂等：placement 已存在且目标相同 → `already_materialized`；目标不同 → 拒绝
  （不去改别人的 placement）。
* **不伪造 held**：物化只写 `room`，手持必须由 Resolver 的 `PICK_UP` 产生。
* 崩溃/失败窗口：钱与产权已在经济里（`applied`），placement 缺失 →
  `inventory` 报 `PENDING_MATERIALIZATION` 且不可用；同 key 重放 `purchase()`
  会重新尝试物化并补完。
* **物化只在 POST 发生**：只有 `purchase()`（含同 key 重放）会写 substrate。
  `wallet()` / `shop()` / `inventory()` / `operation()` 是 GET，全部只读：
  `SubstrateStore.load()`（该 load 不重写文件；旧 schema 只在内存里升级），
  不会触发 schema 迁移落盘、不建目录、不写 placement，也不需要 P 在 GET 里补任何写。
* **`consumed` 是终态，物化不得复活它**：`materialize_purchase()` 先看 substrate
  回执——只要有回执名字指向该 instance，就返回 `rejected / ALREADY_CONSUMED`
  （`materialization="consumed"`、`receipt_id`），**在 placement 判断之前**，因此绝不会
  因为"placement 不在了"而重新 `place_object`。已消耗的 instance 永远不再拥有 placement。

## 5. 失败码一览（手机面）

| code | 含义 |
|---|---|
| `FEATURE_DISABLED` | `CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED` 未开 |
| `GENESIS_NOT_INITIALIZED` | 没有经济 genesis（读不初始化） |
| `LOCAL_OWNER_CONTEXT_INVALID` / `LOCAL_OWNER_UID_MISMATCH` / `LOCAL_OWNER_SUBJECT_MISMATCH` / `LOCAL_OWNER_HOME_MISMATCH` | 本地 owner 身份不成立 |
| `LOCAL_OWNER_PHONE_NOT_BOUND` | 服务端未显式绑定手机 owner 命令 |
| `LOCAL_OWNER_VERB_NOT_ALLOWED` | 手机 owner 命令只允许 `CONSUME` |
| `UNKNOWN_CATALOG_ITEM` / `ITEM_NOT_SOLD_IN_PHONE_STORE` | 目录/渠道 |
| `INSUFFICIENT_FUNDS` | 余额不足 |
| `ECONOMY_CAPACITY_EXCEEDED` | 256 上限已满（不清理） |
| `PHONE_PURCHASE_OPERATION_CONFLICT` | 同 key 不同商品 |
| `PURCHASE_LEDGER_INCONSISTENT` | 流水与 item 不成对（不崩、不二次扣钱） |
| `WORLD_BODY_SERVICE_UNAVAILABLE` | 没有可信本地 socket（不自动起服务） |
| `ALREADY_CONSUMED` / `NOT_OWNED` / `PURCHASE_VOUCHER_MISSING` / `NOT_MATERIALIZED` | 使用前置条件 |
| `STALE_STATE` / `NOT_REACHABLE` / `NOT_CONSUMABLE` / `NOT_ALLOWED` | Resolver 原码，原样透出 |
| `OPERATION_TARGET_CONFLICT` | 同 key 已消耗的是**另一个** instance（不是 `already_applied`） |
| `OPERATION_KEY_CONFLICT` | 同 key 已被另一种操作占用（purchase ↔ use 交叉复用） |

## 6. 世界声明（P 的 demo 必须提供）

`<world_home>/state/world_object_capabilities.json`：

```json
{
  "schema_version": "world.object.capabilities.v1",
  "objects": {},
  "consumables": {},
  "consumable_catalog": {
    "virtual_snack_wafer": {"consumable": true, "free_to_consume": false,
                            "consumption_permit": "owned_purchase"}
  }
}
```

`consumable_catalog` 按 **catalog_item_id** 声明（实例 id 由购买时派生，事先不可知）；
`consumables`（按实例 id 声明）与 `objects`（M15 六键能力）语义不变。旧的
`consumables` 里若出现 `free_to_consume=true` 的条目，行为与 v0.3 一致。

## 7. 不做什么

无真实支付/充值/外发/平台账号；不改物理店营业/位置规则；不新增经济或消耗 store；
不把本地 owner 标成 main-self；不在手机侧直接改 substrate/economy state；
不自动启动 WorldBodyService；不清理 256 上限旧记录。
