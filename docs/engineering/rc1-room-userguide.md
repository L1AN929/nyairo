# rc1 房间生活链：手机使用指南（P 侧）

这一页只讲**手机/工具这一层**怎么用、会看到什么、被拒是什么意思。商店营业时间、价格、交付时长、
灯的光档、身体反应都属于世界/身体 owner（C），手机只如实显示、不替它们算。

## 1. 打开手机

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance <既有实例根> --port 0 --tick-seconds 5 --with-room-living
```

`--with-room-living` 是这一轮（实体店 / 交付 / 布置 / 房间灯）**唯一**的显式开关。它必须写在
命令上，不能只靠环境变量：`serve` 起的 World/Body 子进程的 `CHIYO_WORLD_DAILY_RHYTHM_ENABLED`
一直被写死成 `0`，而手机面读的是实例自己的 `features`（旧实例里记着 `0`，且 features 压过环境变量）
——两边各说各话，实体店永远开不起来。现在交给两端的都是同一组「有效开关」，并且**只在本进程内存里
生效**：`instance.json` 的 `features` 不会被改写，旧实例记录什么还是什么。

不带这个 flag 的启动与旧行为完全一致（房间链关闭）。

命令会打印 `url`（端口可能每次都变）与 `room_living`（这次到底开没开）。浏览器只开这一个
`http://127.0.0.1:<port>/`；前台 Ctrl+C 停，等它自己的两个子进程退出。**不要**对已有实例再跑
`prepare-demo`（会重置，被 `ROOT_NOT_EMPTY` 拒是正确行为）。

开新实例才需要（不带 flag 就是默认关）：

```bash
python3 scripts/phone.py prepare-demo --root /绝对/新的/空目录 --with-world
```

## 2. 一轮完整的「去店里买东西、回家布置、用起来」

1. **看余额**：主屏钱包显示真实余额与流水条数（这是经济 owner 的话，手机不四舍五入、不补零）。
2. **去实体店**：世界页 → 移动 → 街道 → 日用店（一次一条边，分段走；快照里给的是真实步时）。
   到了以后快照会显示 `home_goods_store`。
3. **看货架**：`GET /api/phone/store` 就是店里货架：`store_id`、`availability`（营业中/关门及
   原因码）、`currency`、`catalog[]`（每件带 `catalog_item_id` 与价格）。关门时**不能**买，
   被拒是 `STORE_CLOSED`，不是手机坏了。
4. **买一件**：

   ```bash
   curl -s -X POST http://127.0.0.1:<port>/api/phone/store/purchases \
     -H 'Content-Type: application/json' -H "X-CSRF-Token: <csrf>" \
     -d '{"catalog_item_id":"simple_desk","operation_key":"counter-desk-1"}'
   ```

   - `applied` = owner 已在柜台成交（扣款、产权、交付登记都是它做的）。
   - **不等于到货**。是否在途、还有多久、到了没有，只看世界快照。
   - 同一次意图重发**必须用同一个 `operation_key`**：会回 `already_applied`，不会二次扣钱。
     换商品却用同一个 key 会被拒（key 冲突），这是保护不是 bug。
   - 客户端不能带 `price`/`balance`/`uid`/`owner`/`home`/`elapsed*`/身体值：带了就是
     `CLIENT_IDENTITY_REFUSED`(409)。
5. **交付**：世界快照里 `deliveries`（`/state` 的 `apps.world.data.room.deliveries` 是它的摘要）
   显示在途/已到。**刷新页面不会让东西提前到**：到货只由世界服务自己的 tick 推进。
   如果这一块是 `NOT_REPORTED`，意思是「服务没有提供这项」，不是「没有在途」。
6. **回家布置**：到了以后用世界动作把自有家具搬进房间或放到已声明的承载面上：

   ```bash
   curl -s -X POST http://127.0.0.1:<port>/api/phone/world/actions \
     -H 'Content-Type: application/json' -H "X-CSRF-Token: <csrf>" \
    -d '{"action":"ARRANGE","params":{"object_id":"<自有家具 object id>",
          "target":{"kind":"room","location_id":"home","area_id":"bedroom"}},
          "operation_key":"arrange-desk-1"}'
   ```

   - 动词就是 `ARRANGE`（世界 owner 已定名，参数恰好 `object_id` + `target`）；快照的
     `actions.verbs` 是权威，`RELOCATE` 这类别名手机不接受，也不替你改名。
   - 目标只能是 `room` 或 `surface`（`surface_id` 必须已被世界声明）；`container` 一类会 400。
   - 别人的东西、未到货的东西、已消耗的东西都会被 owner 拒（`NOT_OWNED` / `NOT_DELIVERED` /
     `ALREADY_CONSUMED` 等），手机不改写原因码。
   - 固定家具**不能**「拿在手里」（`PICK_UP` 一定拒），搬家只能走这个动作。
7. **把东西放到桌面 / 取回**：先把小件 `PICK_UP` 到手里，再 `PLACE` 到已声明的 `surface`
   （例如 `desk_surface`），之后可以再 `PICK_UP` 取回。承载面必须是**这一件**家具提供的：
   两张桌子不会共用一块面。
8. **坐下来 / 休息**：`POSE {pose:"seated"}` 是姿势；`REST {state:"start"}` 还需要既有门槛
   （疲劳度、在有床的面上等）。手机不会为了让页面好看而假装恢复。
9. **开关自己的灯**：

   ```bash
   curl -s -X POST http://127.0.0.1:<port>/api/phone/world/actions \
     -H 'Content-Type: application/json' -H "X-CSRF-Token: <csrf>" \
     -d '{"action":"TOGGLE_FIXTURE","params":{"object_id":"<lamp object id>","state":"on"},
          "operation_key":"light-lamp-1-on"}'
   ```

   - `state` 是**绝对状态**，只能是 `on` 或 `off`；`toggle`、`ON`、布尔值都会被拒
     （`FIXTURE_STATE_INVALID`）。想关灯就发 `off`，不要靠「再切一次」。
   - 同一状态重发 → 世界会告诉你已经是这样（noop），不会产生第二条后果。
   - 灯必须是她自己的、已到货、就摆在**当前这个房间**；卧室的灯不会照亮客厅。
   - 房间光未知时快照给 `unknown`/`NOT_REPORTED`，手机**不会**显示成「关」或「0」。
   - 光档（dim/moderate/bright）是世界声明的固定属性，dim 不触发阈值是正常结果——
     不会为了页面好看改成 bright。
10. **刷新 / 重启承接**：Ctrl+C 停 serve，用同一条命令（含 `--with-room-living`）重启同一个实例；
    余额、物件、布局、灯状态、操作回执都应在。重启只改 `instance.json` 里
    `supply_socket`/`supply_generation` 两行（为了不删除旧 socket），旧 socket 文件会被改名保留；
    `features` 一个字节都不改。
11. **想把这轮功能关掉**：核验「这个 PID 确实是这个实例的 serve」后正常停止，再用**同一个 root、
    不带 `--with-room-living`** 的命令重启，就是功能回退（不带 flag 就是房间链关闭，与旧启动一致）。
    这**不是**数据回滚：已经发生的交易、产权、摆放、灯状态与执行账本全部保留，不覆盖任何数据档；
    如果 schema 兼容性不确定，先只读报告阻塞，**不要**自动拿旧代码或旧备份回写。
12. **备份是取证用的，不是回退按钮**：`instance.json` / `world_property_economy.json` /
    `body_substrate.json` / `world_state.json` 的备份可以预先做，但本轮之后会有新交易、新摆放、
    新的开灯记录，覆盖旧备份会把余额、产权与历史一起倒退。真要恢复，先核对当前 ledger
    （含未决动作与删除屏障）与之后的全部历史，再决定是否恢复、恢复到哪一刻——
    **数据回滚 ≠ 功能回退**。

## 3. 工具（她自己在对话里用）

`chiyo_phone` 只多两个动作：

| 动作 | 作用 | 说明 |
|---|---|---|
| `action=store` | 读实体店货架/营业/货币 | 只读，不需要 `operation_key` |
| `action=store_purchase` | 柜台买一件 | 需要 profile 的 `phone.write_enabled`；`catalog_item_id` 必填，`operation_key` 缺省时按意图派生 |

房间动作仍走 `action=world_action`：

```json
{"action":"world_action","world_action":"TOGGLE_FIXTURE",
 "params":{"object_id":"lamp-1","state":"off"},"operation_key":"light-off-1"}
{"action":"world_action","world_action":"ARRANGE",
 "params":{"object_id":"item-1","target":{"kind":"surface",
          "location_id":"home","surface_id":"desk_surface"}},"operation_key":"arrange-1"}
```

规矩不变：入口必须是绑定过的私聊（`local` 与被委派会话一律拒）、写要有 `write_enabled`、
受限记忆下买/用/柜台购买都要 `phone.private_artifacts`、实例指纹不符直接拒。
返回 `status=unknown` 时**不要换 key 重发**，先 `action=world_operation`（世界动作）或
`action=operation`（购物/消耗）按原 key 对账。

## 4. 常见拒绝码（人话）

| 码 | 意思 | 该做什么 |
|---|---|---|
| `STORE_LOCATION_REQUIRED` | 人不在店里 | 真的走到 `home_goods_store` 再买 |
| `STORE_CLOSED` | 店关门（营业时间之外） | 等它开门，别改价/别绕 |
| `ITEM_NOT_SOLD_AT_STORE` | 这件不在实体店渠道 | 用手机虚拟店渠道的那件 |
| `INSUFFICIENT_FUNDS` | 钱不够 | 看钱包，别造钱 |
| `NOT_DELIVERED` | 还没到货就想摆/用 | 等世界 tick 或看 `deliveries` |
| `ALREADY_CONSUMED` | 已经用掉了 | 它就是终态，不会复活 |
| `NOT_OWNED` / `NOT_AN_ECONOMY_INSTANCE` | 不是她买的东西 | 不要拿世界自带的摆设当自己的 |
| `TARGET_KIND_NOT_ALLOWED:container` | 家具不能塞进容器 | 放到房间或已声明的桌面 |
| `FIXTURE_STATE_INVALID` | 灯只接受 `on`/`off` | 发绝对状态 |
| `NOT_A_FIXTURE` / `FIXTURE_NOT_HERE` | 没有声明成灯 / 灯不在当前房间 | 换当前房间的灯 |
| `PHONE_FACADE_UNIMPLEMENTED` | 这部手机的世界/商店 facade 还没有这个方法 | 说明代码没更新到，不是空结果 |
| `PHONE_WRITE_DISABLED` | profile 没开写 | 在 profile 里显式开 `phone.write_enabled` |
| `CLIENT_IDENTITY_REFUSED:<keys>` | 客户端想替 owner 说身份/价格/时间/身体值 | 删掉这些字段 |

## 5. 不要这么做

- 不要为了「看到东西到货」反复刷新或反复 POST：GET 不推进，重复 POST 只能靠同一个 key。
- 不要换 `operation_key` 重试同一件事（会变成第二次购买/第二个动作）。
- 不要手工编辑 `world_property_economy.json` / `body_substrate.json` / `world_state.json`
  来「修」界面；那是 owner 的唯一真相，改了就再也对不上账。
- 不要删除/清理实例目录里的 socket、日志、缓存或旧备份——本轮的规则是不删任何东西。
- 不要把「布置成功」当成「能用」：能不能坐、能不能放东西、灯亮不亮，都以快照与后续动作为准。
- 不要把「关掉这轮功能」做成「拿旧备份把数据盖回去」：功能回退是正常停机后同 root、
  **不带 `--with-room-living`** 重启（功能回退 ≠ 数据回滚，账/产权/摆放/灯状态都留在原地）；
  覆盖 `instance.json` / 经济 / substrate / world_state 会把余额、产权与历史一起倒退。
- 不要试图只用环境变量 `CHIYO_WORLD_DAILY_RHYTHM_ENABLED=1` 打开实体店：旧实例的 `features`
  压过环境变量、子进程也读同一组记录值，两边都不会变——要么用 `--with-room-living`，要么别开。
