# v0.4 手机虚拟购物（W 写集）——实现结果与真实用法

状态：implemented（代码、契约、测试、note 同一提交事实）
契约：`docs/engineering/v04-phone-shopping-contract.md`（唯一实际契约，P 只按它接线）
决策：`.agents/notes/implemented/architecture/2026-10-20-v04-phone-shopping-owned-purchase-consume.md`

## 1. 交付了什么

| 文件 | 改动 |
|---|---|
| `components/world/scripts/world_phone_shopping.py` | **新增** `PhoneShopping` facade + `materialize_purchase()` + `execution_id_for()`（P 的唯一入口） |
| `components/world/scripts/world_property_economy.py` | 手机渠道常量；目录加 `channels` 与 `virtual_snack_wafer`（240 JPY，非营养）；`_validate_provenance` 接受两种真实购买来源；`shop_projection()` 按物理店渠道过滤；`purchase_item()` 拒跨渠道商品；新增 `validate_local_owner()` / `phone_catalog_entries()` / `phone_shop_projection()` / `phone_purchase_item()` / `find_purchase_intent()`；`_transaction_projection()` 补 `purchase_intent_id` |
| `components/world/scripts/world_consumable_contract.py` | 新 receipt 来源 `purchased_item`；permit 词汇 `owned_purchase`；`purchase_voucher()`；`consumable_catalog` 声明块 + `load_catalog_declarations()`；`declaration_of(..., economy=)`；`consumption_verdict()` 只在有凭证时放行经济物件，其余仍 `ECONOMY_OBJECT` |
| `components/world/scripts/world_action_resolver.py` | 第二 authority `local_owner_phone`（仅 `CONSUME`）；`_local_owner_context()` / `_local_owner_refs()` 记录来源/uid/绑定；`declaration_of` 传经济事实；结果带 `purchase_voucher` / `local_owner`；verdict 码映射 |
| `components/world/service/world_body_gateway_api.py` | 新窄命令 `submit_local_owner_action`（服务端绑定才可用、拒权限字段、只允许 `CONSUME`、仍走一次性授权 registry、盖章 `local_owner_phone`）；`_consume_availability()` 用目录声明 |
| `components/world/service/world_body_service.py` | 从 `CHIYO_WORLD_LOCAL_OWNER_PHONE` 构造服务端绑定并注入 gateway；`act()` 把 resolver 的 `consumption_receipt` / `purchase_voucher` / `object_id` 透出 |
| `tests/test_phone_shopping_owner.py` | **新增** 17 个正负用例（见 §5） |
| `.agents/notes/.../2026-10-20-v04-phone-shopping-owned-purchase-consume.md` | 新增决策 note（含被否决方案） |
| `docs/engineering/v04-phone-shopping-contract.md` | 新增契约（先行落盘，实现与它一致） |

未触碰：`components/phone/**`、`chiyo_bundle/**`、Life 核心、别人的测试。没有新经济/消耗 store，
没有 commit/stage/push，没有真实支付/外发/真模型。

## 2. P 怎么用（真实可用，已跑通）

```python
import os
from pathlib import Path
from world_phone_shopping import PhoneShopping

home = os.environ["CHIYO_WORLD_HOME"]          # == 服务的 world_home
phone = PhoneShopping(
    home,
    os.environ,
    local_owner_uid=os.getuid(),               # 必须是真实 uid
    subject="chiyo",
    gateway_socket=Path(home) / "run" / "gateway.sock",
)

phone.shop()                                    # 浏览（不需要在物理店）
phone.wallet()                                  # 余额 + 流水
phone.purchase("virtual_snack_wafer", key)      # 同 key 重放不二次扣钱
phone.inventory()                               # 真实位置/可用性/是否已消耗
phone.use(item_instance_id, key)                # 经原 Resolver 吃掉一次
phone.operation(key)                            # 用同一 key 对账
```

HTTP 层照抄信封：`{"ok", "status", "reason_code"?, "data"}`；
`/api/phone/operations/<key>` → `phone.operation(key)`。

服务端必须显式打开本地 owner 命令（否则整条命令 `LOCAL_OWNER_PHONE_NOT_BOUND`）：
`CHIYO_WORLD_LOCAL_OWNER_PHONE=chiyo`（服务进程环境，客户端传不了）。服务启动仍由
P 的 demo 负责，`use()` 不会自动起服务，没有 socket 就是
`unavailable / WORLD_BODY_SERVICE_UNAVAILABLE`。

## 3. 真实运行记录（`demo_phone_shopping.py`，fixture `/tmp/nyaw05r.jdK3q/v04-phone-w-demo-*`）

```
genesis: created
service ready: True
shop:        ok=true  status=applied  channel=phone_virtual_store  scheme=virtual_store_24h  purchase_available=true
wallet.before: balance=10000  transactions=[]
purchase:    ok=true  status=applied  price=240  current_balance=9760  ownership=OWNED
             item_instance_id=item-ed46b934…  placement={kind:room, location_id:home, area_id:bedroom}
             materialization=materialized
purchase.replay: ok=true status=already_applied  current_balance=9760  materialization=already_materialized
inventory:   usable=true  delivery_status=DELIVERED  location=room:home/bedroom
             consumption_source=purchased_item  purchase_voucher.transaction_id=purchase-ed46b934…
use:         ok=true  status=applied  applied_to_body=true
             receipt.source=purchased_item  receipt.receipt_id=world-consumption:69b3b337…
             local_owner={source:local_owner_phone, uid:0, binding:{subject:chiyo, world_home:…}}
use.replay:  ok=true  status=already_applied  （同一 receipt_id）
use.again:   ok=false status=rejected  reason_code=ALREADY_CONSUMED
operation(use): ok=true status=applied ledger_state=committed ledger_result=SUCCESS
wallet.after: balance=9760  transactions[0]={kind:PURCHASE, direction:DEBIT, amount:240,
              catalog_item_id:virtual_snack_wafer, purchase_intent_id:demo-buy-1}
```

对应的 Body 后果（`data/body/consequences.jsonl`，实测一条）：

```
action_type=CONSUME  raw_body_values=false  inputs[0]={class:physical_result, effort:light,
movement_duration_seconds:2.0, pose:standing}
notes=["consume_operation_load_model:2.0s manipulation only; not nutrition, not satiety,
        not hunger, not health, not recovery"]
```

即：只有既有的 2s 轻操作量级，没有营养/饥饿/健康/回血数值。

## 4. 手机面失败码（实测可命中）

`FEATURE_DISABLED` / `GENESIS_NOT_INITIALIZED` / `ECONOMY_STATE_ERROR` /
`LOCAL_OWNER_CONTEXT_INVALID` / `LOCAL_OWNER_UID_MISMATCH` / `LOCAL_OWNER_SUBJECT_MISMATCH` /
`LOCAL_OWNER_HOME_MISMATCH` / `LOCAL_OWNER_PHONE_NOT_BOUND` / `LOCAL_OWNER_VERB_NOT_ALLOWED` /
`UNKNOWN_CATALOG_ITEM` / `ITEM_NOT_SOLD_IN_PHONE_STORE` / `ITEM_NOT_SOLD_AT_STORE` /
`INSUFFICIENT_FUNDS` / `ECONOMY_CAPACITY_EXCEEDED` / `PHONE_PURCHASE_OPERATION_CONFLICT` /
`PURCHASE_LEDGER_INCONSISTENT` / `INVALID_OPERATION_KEY` / `INVALID_ITEM_INSTANCE_ID` /
`WORLD_BODY_SERVICE_UNAVAILABLE` / `AUTHORIZATION_REGISTRY_UNAVAILABLE` / `SERVICE_TIMEOUT` /
`PURCHASE_VOUCHER_MISSING` / `PURCHASE_NOT_OWNED` / `NOT_AN_ECONOMY_INSTANCE` /
`NOT_MATERIALIZED` / `NOT_DECLARED` / `NOT_FREE` / `ECONOMY_OBJECT` / `NO_PLACEMENT` /
`NOT_HELD_OR_HERE` / `POSE_CANNOT_CONSUME` / `LOCATION_CLOSED` / `ALREADY_CONSUMED` /
`NOT_EXPOSED` / `OPERATION_NOT_FOUND` / `LEDGER_CORRUPT` / `NOT_OWNED` / `NOT_DELIVERED` /
`PLACEMENT_CONFLICT` / `SUBSTRATE_STATE_ERROR` / `SUBSTRATE_NOT_INITIALIZED`。

`STALE_STATE` / `NOT_REACHABLE` / `NOT_CONSUMABLE` / `NOT_ALLOWED` 是 Resolver 原码，原样透出。

## 5. 验收：真实命令与结果

```
$ wsl -e bash -lc "cd /mnt/d/nyairo-v04-backend && PYTHONDONTWRITEBYTECODE=1 \
    python3 tests/test_phone_shopping_owner.py"
Ran 20 tests in 1.863s
OK                                                    # exit 0

$ ... python3 -m unittest discover -s components/world/service -p 'test_*.py'
Ran 263 tests in 16.778s
OK                                                    # exit 0（既有 gateway/authorization/bridge 全绿）

$ ... python3 -m unittest tests.test_world_consume tests.test_world_consume_faults \
      tests.test_world_daily_life_consume
Ran 44 tests in 60.828s
OK                                                    # exit 0（v0.3 可食/坏账本/回执/日常消费不变）
```

后两组（263 / 44）是 v1 轮的真实记录；R1 只改了 `world_phone_shopping.py` 与文档，
按父级要求**没有**重刷这两套，也没有重跑其它旧套件。

20 个用例逐条对应施工要求：

| 用例 | 固定的事实 |
|---|---|
| `test_shop_is_its_own_channel_and_read_does_not_initialize` | 手机店自declared 24h、只卖自己的目录；读不建经济、不发钱 |
| `test_physical_store_channel_is_untouched` | 物理店规则不变；跨渠道购买被拒；余额不动 |
| `test_purchase_is_atomic_materialized_and_owned` | 一次原子：流水+OWNED item；物化到 `home/bedroom`；不写 `hand`；钱包流水可见 |
| `test_same_key_replay_never_charges_twice` | 同 key → `already_applied`，一笔流水、一件物、余额不变 |
| `test_same_key_different_item_is_a_conflict` | 同 key 换商品 → `PHONE_PURCHASE_OPERATION_CONFLICT` |
| `test_insufficient_funds_and_capacity` | 余额不足拒绝；256 上限拒绝且不清理 |
| `test_broken_economy_is_never_read_as_empty` | 坏账本 → `ECONOMY_STATE_ERROR`，文件原样不动 |
| `test_unpaired_ledger_document_is_refused_not_raised` | schema 合法但流水/物件不成对 → `PURCHASE_LEDGER_INCONSISTENT`，不抛异常、不扣钱 |
| `test_uid_and_subject_cannot_be_forged` / `test_bad_home_is_refused` / `test_request_uid_is_not_an_identity` | uid/subject/home 不能伪造，请求体 uid 不是身份 |
| `test_use_requires_a_real_service` | 没有 socket → 不自动起服务、Body 一步不动 |
| `test_unmaterialized_instance_is_not_usable_and_purchase_recovers_it` | 未物化不可用；同 key 重放补物化且不二次扣钱 |
| `test_consume_once_through_the_resolver` | 原 Resolver 一次消耗一条 receipt、`source=purchased_item`、`local_owner` 记录、同 key `already_applied`、换 key `ALREADY_CONSUMED`、经济仍 OWNED、Body 只 2s 轻操作、`operation()` 对账 |
| `test_use_refused_when_body_is_not_here` | 位置不符 → `NOT_HELD_OR_HERE`，零回执零后果 |
| `test_economy_instance_without_declaration_is_still_refused` | 未声明 → `NOT_DECLARED` |
| `test_free_declaration_does_not_open_an_economy_instance` | 世界声明"免费"也不能吃经济物件 → 仍 `ECONOMY_OBJECT`（v0.3 铁律默认保留） |
| `test_get_operation_does_not_materialize_and_post_recovers` | GET 各接口不改 substrate 字节、不物化；只有 POST 同 key 恢复 |
| `test_same_key_with_a_different_target_is_a_conflict` | 同 key 换 instance → `OPERATION_TARGET_CONFLICT`；B 未耗尽、仍在原位、仍可用 |
| `test_key_cannot_cross_purchase_and_use` | 同 key purchase/use 交叉复用 → `OPERATION_KEY_CONFLICT`（两个方向都拒） |

fixture 全部留在 `/tmp/nyaw05r.jdK3q/v04-phone-w-*`（`unique_home()` 创建一次即保留；没有删除、
没有 unlink、没有清理脚本）。临时探针脚本只在会话 scratch 里。

## 6. 已知上限 / 留给下一轮

1. **交叉对账缺口（实测，未修）**：`world_property_economy.validate_state()` 只用"借方条数 ==
   item 条数"判流水与物件对应，所以一份两者不配对但计数相同的文档是 schema 合法的；旧
   `purchase_item()` 的重放分支对这种文档会抛未捕获的 `StopIteration`。本包不改旧路径（它自己
   永远写不出这种文档），新手机路径返回 `PURCHASE_LEDGER_INCONSISTENT`。要收口应在
   `validate_state` 加"按 intent 配对"，而不是在重放分支加 `try`。
2. **物化窗口**：钱+产权与物化是两次 owner 原子保存。崩在中间的状态是
   `materialization="pending"` + `usable=false`；**只有 POST `purchase()` 用同一 key 重放**
   才会补完（`operation()` 是 GET，只报告 pending 与 `recovery` 提示，不写入）。P 的 demo
   若要在启动时自愈，应对 `pending_count>0` 的条目按其 operation key 重发一次 POST purchase。
   没有后台补偿循环——那是 P/demo 的职责，本包不擅自起线程。
3. **手机渠道只有一个商品**：`virtual_snack_wafer`。加商品只需在 `CATALOG` 加条目 +
   `channels=(CHANNEL_PHONE_VIRTUAL_STORE,)` + 世界声明 `consumable_catalog`，不需要改规则。
4. **`use()` 目前只允许 `CONSUME`**：`submit_local_owner_action` 的动词白名单是
   `("CONSUME",)`。手机端要"拿起来"（PICK_UP）需要另行明确授权面，本轮没有偷偷放宽。
5. **门禁脚本（R1 修正）**：AGENTS 的 write-notes 门禁**确实存在**，位于
   `C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\`（不在本仓库树内，v1
   报告写成"不存在"是错的）。从仓库 cwd 实跑：

   ```
   cd D:\nyairo-v04-backend
   node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-tree.ts
     ok: 31 note(s) tree and relative links verified        (exit 0)
   node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-format.ts
     ok: 31 note(s) verified                                (exit 0)
   ```

## 7. R1 定向修正（父级唯一一轮集成检查）

只动 W 写集的两个实际缺陷，不扩权限、不扩商品、不加拿取。

**缺陷 1：GET 会隐式物化。** `operation()` 旧实现在购买分支里直接调
`materialize_purchase()`（写 substrate），而它被 `GET /api/phone/operations/<key>`
使用 —— GET 变成了写。现在：

* `operation()` 只读（经济流水 + substrate placement/receipts），缺物化时返回
  `materialization="pending"`、`materialization_reason_code="NOT_MATERIALIZED"`、
  `recovery="POST /api/phone/purchases with the same operation_key"`（事实说明，不是让 P 在
  GET 补写）、`consumed`、`receipt`；
* 新增 `_read_placements_and_receipts()`：`wallet/shop/inventory/operation` 全部走只读
  `SubstrateStore.load()`。**实测 `load_with_migration()`/`load()` 从不重写文件**（旧 schema
  只在内存里升级），所以 GET 不会触发 schema 迁移落盘、不建目录、不改 substrate 字节
  （测试用 `read_bytes()` 前后比对）；只有 POST `purchase()`（含同 key 重放）会物化。

**缺陷 2：同 key 换目标被当成 already_applied。** 旧 `use()` 只要发现该 execution 有回执就
返回成功，不比对回执属于谁 —— 同 key 先吃 A、再拿 B 会伪装成 B 成功。现在：

* 先比对 `receipt.object_id == 请求 item`，不同 → `rejected / OPERATION_TARGET_CONFLICT`
  （带 `object_id`、`requested_object_id`、`receipt_id`）；B 不消耗、无回执、原位、仍 `usable`；
* 跨操作键：key 已在经济里是购买 → `use()` 直接 `OPERATION_KEY_CONFLICT`
  （`data.operation="purchase"`）；key 派生的 execution 已在执行账本 → `purchase()` 直接
  `OPERATION_KEY_CONFLICT`（`data.operation="use"`）；
* 结论写进契约：**一个 key 只命名一个操作、一个目标**。

**新增测试 3 例**（`PhoneReadOnlyAndKeyTests`）：`test_get_operation_does_not_materialize_and_post_recovers`、
`test_same_key_with_a_different_target_is_a_conflict`、`test_key_cannot_cross_purchase_and_use`。
与既有 17 例一起实跑一次：`Ran 20 tests in 1.863s / OK`（exit 0，`.pyc` 数量运行前后都是 15）。按父级要求未重刷 263/44 大套；
没有生成 `.pyc`（语法检查用 `ast.parse`/直接跑测试，不用 `py_compile`），也没有删除任何已有
`.pyc`。

## 8. R2 定向修正（W 最后一个集成缺陷：耗尽态）

**两个缺陷同源**：消耗后 placement 被删这一个事实，被读成了两种意思。

**缺陷 1：inventory 自相矛盾。** 旧代码先无条件写
`materialization = "pending"`（因为 placement 没了），再在 consumed 分支只改
`delivery_status="CONSUMED"`，于是同一个条目同时说"已吃掉"和"待物化"，`pending_count`
也把耗尽物算进去。现在 `consumed` 是唯一终态：
`materialization="consumed"`、`location="consumed"`、`delivery_status="CONSUMED"`、
`consumed=true`、`receipt_id`（原回执），`pending_count` **不含**耗尽条目。

**缺陷 2：同 key POST 重放会复活物件。** `materialize_purchase()` 旧逻辑只看
"placement 不存在 + 经济记录还是 `OWNED` at `home/bedroom`"，于是消耗后同 key 重放
`purchase()` 会重新 `place_object`，凭空变出一个新 placement。现在在 placement 判断
**之前**先查 substrate 回执：只要有回执名字指向该 instance →
`status="rejected"`、`reason_code=ALREADY_CONSUMED`、`materialization="consumed"`、
`receipt_id`，**绝不** `place_object`。`purchase()` 把这种重放映射成
`status="already_applied"` + `data.materialization="consumed"` + `consumed=true` +
`delivery_status="CONSUMED"` + `location="consumed"` + `receipt_id`，且**不含**
`placement` / `materialization_reason_code`；`operation()`（GET）同理报告终态、不再给
`recovery` 提示。

**定向测试**（只跑这一个 case + 前次 3 个键/query case，不重刷 20/263/44）：

```
$ wsl -e bash -lc 'cd /mnt/d/nyairo-v04-backend && PYTHONDONTWRITEBYTECODE=1 \
    python3 -m unittest tests.test_phone_shopping_owner.PhoneReadOnlyAndKeyTests'
test_consumed_instance_is_terminal_and_replay_does_not_resurrect ... ok
test_get_operation_does_not_materialize_and_post_recovers ... ok
test_key_cannot_cross_purchase_and_use ... ok
test_same_key_with_a_different_target_is_a_conflict ... ok
Ran 4 tests in 1.703s
UNITTEST_RC=0
```

该 case 固定的事实：buy → use（一条回执、placement 消失）→ inventory 无 pending 且
`materialization="consumed"` / `location="consumed"` → 同 key `purchase()` 重放仍是
`already_applied` 且为 consumed 终态、无 `placement` 字段 → `placement(item) is None`
（无复活）→ substrate 字节与 `world_object_capabilities.json` 字节**逐字节不变**（无新
placement、无新声明）→ `consumptions` 仍 1 条且是原回执 → 经济流水仍 1 笔、item 仍
`OWNED`、余额不变 → `operation()` 也是终态且无 `recovery` 提示。

测试文件现在共 21 例（20 + 本次 1），但本轮**只跑了上表 4 例**；未重跑其它套件、未用
`py_compile`、未删除任何文件、未发布。
