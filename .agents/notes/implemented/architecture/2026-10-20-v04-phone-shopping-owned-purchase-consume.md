# Agent Note: v0.4 手机虚拟购物——「经济物件一律禁食」的精确开放与购得物物化

Status: implemented

## Problem

v0.3 的 `CONSUME` 只有一条可食规则：物件必须由世界声明 `consumable=true` 且
`free_to_consume=true`，并且**不是**经济物件（`ECONOMY_OBJECT` 一律拒）。它在当时是对的：
世界没有支付/产权消费 authority，把价格读成"免费"就是伪造事实。

v0.4 要交付一个真实可感知的闭环：手机上有一家**自己的**虚拟线上店，用同一份钱包真实扣款
买到一份点心，买到的东西真的出现在家的位置上，之后能经原 Resolver 吃掉它一次并拿到回执。
如果继续"经济物件一律禁食"，这条链只能靠伪造成立；如果直接把规则放宽成"买了就能吃"，
就会把 v0.3 那条铁律拆掉。同一轮还要解决三个具体缺口：

1. **没有手机渠道**：`world_property_economy` 只有物理店 `home_goods_store`，购物必须在店里、
   且受 `world_daily_rhythm` 营业时间约束，在家里下单只能绕规则或改物理店规则；
2. **没有"物化"**：购买只写经济文档（`current_location = home/bedroom`），substrate 里没有
   任何 placement，于是"拥有"被当成了"在手边"，任何消耗判定都会在真实位置上失败或被迫伪造
   `hand`；
3. **没有本地 owner 入口**：手机命令来自本地 owner（不是模型 MainSelf），既不能复用
   `purchase_item(main_self_verified=True)`（那会把用户操作标成 main-self），也不能在手机侧
   直接改世界状态。

## Decision

一个 store、一个 owner 写口、一条窄开的口子。

1. **手机渠道是显式新增的虚拟店**（`CHANNEL_PHONE_VIRTUAL_STORE = "phone_virtual_store"`）。
   它自己声明 `availability = {status: OPEN, scheme: "virtual_store_24h"}`，自己是 24h
   虚拟店这件事实而不是绕过物理店；物理店 `_store_check`（在店 + 营业时间）一字未改，
   `shop_projection()` 只过滤渠道、不加新规则。目录按 `channels` 归属：原 6 件家居仍是
   `home_goods_store`，新增 `virtual_snack_wafer`（240 JPY、手持可携带、**非营养**）只属于
   手机渠道。跨渠道购买分别被 `ITEM_NOT_SOLD_AT_STORE` / `ITEM_NOT_SOLD_IN_PHONE_STORE` 拒。
2. **手机购买是经济 owner 的一次原子事实**：`phone_purchase_item()` 在同一把锁、同一次
   `_save()`（tmp + fsync + replace）里写一笔 `PURCHASE/DEBIT` 流水和一个 `OWNED` item；
   `operation_key` 直接就是经济的 `purchase_intent_id`，所以同 key 重放
   `already_applied`、绝不二次扣钱。没有新 store、没有第二份产权真相。
3. **本地 owner 身份只由服务端构成**：`PhoneShopping(hermes_home, environment, *,
   local_owner_uid, subject, gateway_socket)` 在构造时校验真实 `os.getuid()`、`subject`、
   绝对且存在的 home；经济 owner 再有 `validate_local_owner()` 复核（source/uid/subject/home）。
   请求体里的 `uid`/`authority`/`main_self_verified` 从来不是身份。流水 provenance 写
   `source = "local_owner_phone_purchase"`、`delivery = "phone_virtual_store_home_delivery"`，
   与旧 main-self 购买（`verified_main_self_buy_item`）在 schema 层就分得开。
4. **物化是 substrate owner 自己的写**：`world_phone_shopping.materialize_purchase()` 用
   `place_object({kind: "room", location_id: "home", area_id: "bedroom"})` + 一次带
   `expected_revision` 的 `SubstrateStore.save()`。**不写 `hand`**：手持只能是 Resolver 的
   `PICK_UP` 结果。跨 store 不假装 ACID——钱与产权已在经济里、placement 缺失就是
   `PENDING_MATERIALIZATION`（`usable=false`），同 key 重放 `purchase()` 只补物化不再扣钱。
5. **唯一被开放的可食路径带凭证**：声明可用 `consumption_permit = "owned_purchase"`
   （`free_to_consume=false`）。`world_consumable_contract.purchase_voucher()` 要求
   item `acquisition_kind=PURCHASE` + `disposition=OWNED` + 一笔 `PURCHASE/DEBIT` 流水
   三处对齐（同 `purchase_intent_id`/`catalog_item_id`/`transaction_id`）。通过则 receipt
   `source = "purchased_item"`；不通过分别 `PURCHASE_VOUCHER_MISSING` /
   `PURCHASE_NOT_OWNED`。没有 permit 的经济物件仍然 `ECONOMY_OBJECT`——旧的"一律禁食"作为
   默认规则原样保留，只有精确声明 + 精确凭证才例外。声明按 **catalog_item_id** 放在同一份
   `world_object_capabilities.json` 的新块 `consumable_catalog`（实例 id 是购买时才派生的，
   事先不可知）；`consumables`/`objects` 语义不变。
6. **消耗仍由原 Resolver 提交**：手机侧只做只读预判（同一 `consumption_verdict`），真正提交走
   新窄命令 `submit_local_owner_action`（0600 unix socket + `SO_PEERCRED` uid 检查 + 既有
   `AuthorizationRegistry` verify/consume 不绕过），盖章 `authority = "local_owner_phone"`
   （**不是** `verified_main_self_tool`），动词白名单只有 `CONSUME`，请求里带任何权限形状字段
   直接 `invalid_request`。Resolver 只对 `CONSUME` 接受这个 authority，并把
   `source/uid/binding` 写进 committed ledger 的 `cross_authority_refs` 与结果 `local_owner`。
7. **耗尽真相在 substrate**：消耗不改经济记录（item 仍 `OWNED`），只有
   `substrate.consumptions[execution_id]` 是"吃掉了"的证据；同 key 重放
   `already_applied`，换 key `ALREADY_CONSUMED`，Body 只拿既有 2s 轻操作后果（无营养/回血）。
   `operation(key)` 完全派生：先查经济购买，再查执行账本的 reconcile 结论，都没有才
   `OPERATION_NOT_FOUND`，不新建操作表。
8. **256 上限不清理**：`MAX_TRANSACTIONS`/`MAX_ITEMS` 到顶时明确
   `ECONOMY_CAPACITY_EXCEEDED`，不裁剪旧记录（裁剪等于悄悄放弃重放保护）。
9. **GET 只读、物化只在 POST**（R1 修正）：`wallet/shop/inventory/operation` 只读
   `SubstrateStore.load()`（该 load 不重写文件，旧 schema 只在内存里升级），任何 GET 都
   不物化、不迁移落盘、不建目录；`operation()` 对未物化的购买只报告
   `materialization="pending"` + `recovery="POST …/purchases with the same operation_key"`。
   补物化只有一条路：同一 key 重发 POST `purchase()`。
10. **一个 key 只命名一个操作、一个目标**（R1 修正）：同 execution 的回执必须与请求
    instance 一致才算 `already_applied`，否则 `OPERATION_TARGET_CONFLICT`；key 已是购买时
    `use()` 拒、key 派生 execution 已在账本时 `purchase()` 拒，都是
    `OPERATION_KEY_CONFLICT`。旧实现"有回执就成功"会把 `use(A)` 的 key 拿去伪装 `use(B)`。
11. **耗尽态是唯一终态，重放不得复活**（R2 修正）：`consumed` 与 `pending` 互斥 ——
    inventory 对已耗尽物给 `materialization="consumed"`、`location="consumed"`、
    `delivery_status="CONSUMED"`、`receipt_id`，且不计入 `pending_count`；
    `materialize_purchase()` 在 placement 判断之前先查回执，有回执即
    `rejected / ALREADY_CONSUMED`，绝不重新 `place_object`。同 key 重放 `purchase()`
    返回原购买 `already_applied` + consumed 终态，不写声明、不产生第二条回执、不二次扣钱。

## Alternatives considered

- **让经济物件直接可食（删掉 `ECONOMY_OBJECT`）**：最短路径，闭环立刻能跑通，且"她买的东西
  她自己吃"听上去不需要许可。但它把 v0.3 用一整张 note 立起来的铁律一次性删除：从此任何
  经济物件都成了免费食物，坏账本/假声明也不再是问题——因为不再需要凭证。否决：开放必须
  跟着**凭证**走，而不是跟着"经济物件"这个类别走。
- **在 `world_consumable_contract` 里按实例 id 预声明点心**：实现最简单，但实例 id 是
  `_stable_id("item", operation_key)`，购买前不可知；要让声明先于实例，就得让经济在购买时
  顺手写世界声明文档——那是跨 owner 写声明，声明就不再是"世界自己的话"。否决：加
  `consumable_catalog` 块，按目录商品声明，读取时用可证的经济事实把实例映射到目录。
- **手机购买写 `hand` placement，省掉物化与可达性**：一行就能让"买到手"，也立刻可吃。但它
  恰好伪造了本项目最强调的一件事：`held` 是 Resolver `PICK_UP` 的产物，不是购买可以宣告的
  状态；而且没有任何真实"从店里到家里"的过程。否决：物化只写 `room@home/bedroom`。
- **购买时把经济与 substrate 一起写（假装跨 store 事务）**：看起来很 ACID，实现也不难（先写
  经济再写 substrate，失败回滚）。但两个 owner 各自原子保存，第二段写失败时"回滚"要么重写
  经济（丢失并发事实）要么删除 placement（另一次跨 owner 变更）；而且崩在中间依然要回答
  "钱扣了吗/东西在哪"。否决：承认它不是全域事务，用幂等重放 + 可派生的 pending 状态表达。
- **让手机服务自己发一条 `submit_action`（main-self 通道）**：不用改 gateway，只需在手机侧
  铸造 authorization。但那会把"用户在手机上点了吃"记成 `verified_main_self_tool`，于是账本
  与回执都在说一句假话。否决：新增 `submit_local_owner_action` 与独立 authority，旧通道
  行为不变。
- **把 256 上限改成"满了就清最旧"**：能让长期运行不阻塞。但清的是一条已经发生的 execution
  的重放保护，等于用静默丢历史换可用性。否决：明确拒绝码，让 owner 自己决定。
- **顺手修 `purchase_item` 重放路径的 `StopIteration`（无对应 item 时 `next()` 直接抛）**：
  看起来是同一个 bug 家族。实测（见 Consequences）它需要一份 `validate_state` 接受、但
  流水与 item 不对应的文档；旧 main-self 路径从来不会写出这种文档，因此本轮**不改旧行为**，
  只在自己的新路径用容错查找返回 `PURCHASE_LEDGER_INCONSISTENT`，并把这条已探明的事实记在
  这里，避免下一个人"顺手"改错地方。

## Consequences

- **收益**：手机 → 真实扣款 → 真实产权 → 真实物化 → 原 Resolver 消耗 → 真实回执，整条链
  没有第二个经济 store、没有伪造的 `held`、没有把用户操作标成 main-self。v0.3 的
  `ECONOMY_OBJECT` 默认仍然生效（测试 `test_free_declaration_does_not_open_an_economy_instance`
  固定它），open 的只有 `consumption_permit="owned_purchase"` + 凭证对齐这一条。
- **代价**：经济 schema 多了两种合法 provenance（`local_owner_phone_purchase` /
  `phone_virtual_store_home_delivery`），消耗 receipt 多了第三种 `source`
  （`purchased_item`），声明文档多了一个 `consumable_catalog` 块；`world_consumable_contract`
  与 `world_action_resolver` 各多一个分支；gateway 多一条窄命令与一个 env 开关
  `CHIYO_WORLD_LOCAL_OWNER_PHONE=<subject>`（未设置即整条命令不可用，fail closed）。
- **R1 代价**：`operation()` 不再顺带补物化，因此"GET 看到 pending"与"物化完成"之间只能靠
  POST 推进；这是有意的（GET 必须只读），代价是恢复需要一次显式 POST 重放。跨操作键与
  target 冲突各多一次只读查询（经济 state 一次、执行账本一次），都在同一次请求内完成。
- **已知上限（实测）**：`validate_state()` 只用"借方条数 == item 条数"校验流水与物件的
  对应关系，所以一份 256 条借方 + 256 个 item、但彼此不对应的文档是 schema 合法的；旧
  `purchase_item` 的重放分支对这种文档会抛 `StopIteration`（未捕获）。本包不改它（旧路径
  永远写不出该文档），新路径返回 `PURCHASE_LEDGER_INCONSISTENT` 并在
  `tests/test_phone_shopping_owner.py::test_unpaired_ledger_document_is_refused_not_raised`
  里固定这条事实。若以后要收口，应在 `validate_state` 里加"按 intent 配对"校验，而不是在
  重放分支里加 `try`。
- **升级触发条件**：如果将来出现"一份多口吃完"（servings）、退款、或真正的配送时长，应在
  `world_consumable_contract` 单点扩字段并同步升级 substrate receipt schema，同时保持
  "receipt 是唯一耗尽真相"；手机渠道要新增商品时只加目录项与 `channels`，不要碰
  `_store_check`。
