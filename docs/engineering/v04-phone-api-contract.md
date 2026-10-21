# v0.4 手机 HTTP API 契约（P 的实现契约）

> 本文是「P（phone server / demo / 工具）」这一侧的**实现契约**：`scripts/phone.py`
> 的 `prepare-demo` / `serve` 行为、实例格式、`/api/phone/*` 每个字段、错误语义与安全闸门。
> World 侧（余额、目录价、产权、物化、消耗）**只**按
> `docs/engineering/v04-phone-shopping-contract.md` 与
> `components/world/scripts/world_phone_shopping.py::PhoneShopping` 接线；本文不重新定义
> 经济字段、不新增价格/余额/购物逻辑。前端只消费本文的 HTTP 面
> （`components/phone/web/` 是唯一的 UI 写集）。
>
> 状态：implemented（代码与本文件同一提交事实；结果见
> `docs/engineering/v04-phone-api-result.md`）。

## 0. 它是什么 / 不是什么

* 是：一部**本机**手机服务。它把一个**真实**的 Life Supply `ManagedArtifact`（私人日常
  事项文档 = 笔记/收藏/阅读）和 W 的 `PhoneShopping`（钱包/虚拟店/物品/消耗）拼成一个
  loopback HTTP 面，供浏览器 UI 和个体自己的 `chiyo_phone` 工具使用。
* 不是：新的 Store。笔记/收藏/阅读真相在 Supply ManagedArtifact，钱与物在
  `world_property_economy`，位置与消耗回执在 `world_body_substrate`，提交与幂等在
  `world_action_resolver` + `world_execution_ledger`。手机侧**没有**本地平行 JSON 库，
  也没有「手机 authority」。
* 是：`read_only:false` 的本地 owner 服务。它只对**显式绑定的本地 profile/uid** 开放；
  不是远程面板，不 mock 成功，不自动初始化经济，不自动每天发钱。

## 1. 实例与目录格式

`prepare-demo --root <绝对路径>` 创建、**只创建一次**，已存在（或非空）即拒绝、绝不覆盖：

```
<root>/
  instance.json          # 手机实例（本文定义，见 1.1）
  one-day/               # 真实 Life 实例（one_day_life.prepare 的产物）
    instance.json        #   world_home / workspace_id / artifact_id / grants …
    world-home/          #   真实 World home：data/world/world_state.json、substrate、ledger
    world-package/       #   worldgen 生成的真实世界包
  supply/
    data/                #   真实 Life Supply owner 的 canonical 库（单库）
    run/supply.sock      #   （serve 时由子进程绑定）
  run/
    supply-<generation>.sock  #   Life Supply owner socket（每代一个，全部保留）
    phone.serve.json     #   serve 自己写的 pid/port/started_at（只读提示）
  phone/
    operations.jsonl     #   手机侧操作来源日志（只追加；见 5.3）
  logs/
    supply.log
    world.log
    serve.log
```

### 1.1 `instance.json`（手机实例）

```json
{
  "schema_version": "phone.instance.v1",
  "kind": "phone_local_instance",
  "created_at": "<iso>",
  "name": "<root 目录名>",
  "mode": "local_owner",
  "subject": "chiyo",
  "local_owner_uid": 1000,
  "profile_home": "<root 绝对路径>",
  "root": "<root 绝对路径>",
  "world_home": "<root>/one-day/world-home",
  "world_id": "……",
  "world_package": "<root>/one-day/world-package",
  "supply_socket": "<root>/run/supply-<generation>.sock",
  "supply_generation": "<generation>",
  "supply_data_root": "<root>/supply/data",
  "workspace_id": "……",
  "artifact_grant_id": "……",
  "private_items_artifact_id": "……",
  "reading_artifact_id": "…或 null",
  "work_artifact_id": "……",
  "world_body_socket": "<world_home>/run/gateway.sock",
  "economy_state": "<world_home>/state/world_property_economy.json",
  "capabilities": "<world_home>/state/world_object_capabilities.json",
  "features": {
    "CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED": "1",
    "CHIYO_WORLD_LIVING_SKELETON_ENABLED": "1",
    "CHIYO_WORLD_PACKAGE": "<world_package>"
  }
}
```

* `local_owner_uid` 是 `prepare-demo` 当时的真实 `os.getuid()`。`serve` 用**自己的**
  `os.getuid()` 复核：不一致就直接退出（`exit 3`，`LOCAL_OWNER_UID_MISMATCH`），不做
  任何降级服务。
* `profile_home` 是这个实例自己的 profile 目录。`chiyo_phone` 工具只在 profile 的
  `phone.profile_home` 与实例一致时把请求发过来（见 7）。
* `artifact_grant_id` / `private_items_artifact_id` / `workspace_id` 是**显式绑定**：手机
  只读写这一个 artifact，永不 `latest_resumable` 猜作品。

## 2. `prepare-demo`

`python scripts/phone.py prepare-demo --root <新绝对根> [--seed N] [--theme T]`

1. `--root` 必须绝对、且**不存在或为空**；否则 `exit 2`，`ROOT_NOT_EMPTY`，一个字节不动。
2. 起一个**真实** Life Supply owner 子进程（`life_supply` 的 socket 服务，`LIFE_SUPPLY_SOCKET`
   = `<root>/run/supply-prepare-<generation>.sock`，data root = `<root>/supply/data`，开关
   `LIFE_SUPPLY_SWITCH_WORKSPACE_PROVISION=ON` / `LIFE_SUPPLY_SWITCH_ARTIFACT_EXTERNAL_ACTION=ON`），
   等 socket 就绪。socket 名带 generation：**不覆盖、不删除任何已有 socket**。
3. 用既有 `one_day_life.prepare(...)` 走 owner 自己的命令创建：workspace → artifact →
   reading material → **private daily items artifact** → 真实世界包 + 真实 world home。
4. 用 `world_property_economy.initialize_genesis(hermes_home=<world_home>, environment=…,
   created_at=…)` 生成一次 **真实** 虚拟 genesis（`MOVE_IN_GRANT = 10000 JPY`）。若已存在：
   `already_initialized`，**不重置、不补发**。
5. 写 `<world_home>/state/world_object_capabilities.json`，按
   `v04-phone-shopping-contract.md` §6 声明 `consumable_catalog.virtual_snack_wafer`。
6. 停掉第 2 步的 Supply 子进程（只 SIGTERM 自己起的那个 pid，保留数据；**socket 文件留在
   原地**作为证据，不 unlink）。
7. 写 `<root>/instance.json`，打印单行 JSON 摘要，`exit 0`。
   任一步 owner 拒绝 → 打印失败原因、`exit 1`，**不写 instance.json**。

不创建 profile、不发每日钱、不启动常驻服务、不联网、不接真实支付。

## 3. `serve`

`python scripts/phone.py serve --instance <root> [--port 0]`

* 只绑 `127.0.0.1`（`--host` 不接受其它值）；`--port 0` 由内核分配，**端口打印在
  stdout 的第一行 JSON**：`{"ok":true,"url":"http://127.0.0.1:<port>/","port":<port>,
  "instance":"<root>","pid":<pid>,"supply_generation":"<gen>","supply_socket":"<path>"}`。
* 每次启动都是**新 generation、新 socket 路径**（`<root>/run/supply-serve-<gen>.sock`），
  同一个数据根重启只更新实例里的有效 binding（`supply_socket` / `supply_generation`），
  不重置余额、笔记、物品、流水。
* 自己起、自己管的两个子进程（`serve` 是它们的父进程）：
  * Life Supply owner（同上 env），socket 为本 generation 的新路径；
  * `WorldBodyService`（`runtime_mode=canonical`、`CHIYO_WORLD_BODY_AUTHORITY=production`、
    `CHIYO_WORLD_HOME=<world_home>`、`CHIYO_WORLD_PACKAGE=<world_package>`、
    `CHIYO_WORLD_LOCAL_OWNER_PHONE=chiyo`（**显式授权**手机 owner 命令）、
    `CHIYO_WORLD_PROPERTY_ECONOMY_ENABLED=1`），gateway `<world_home>/run/gateway.sock`。
  * 两个服务都由**显式命令**在这里起；`PhoneShopping` / `use()` 自身**永远不会**去启动
    服务（没 socket 就是 `unavailable / WORLD_BODY_SERVICE_UNAVAILABLE`）。
* **本轮不删除任何 socket**：World 的 gateway/control 若已存在，`serve` 在启动子进程前把它
  **rename** 成 `<name>.retired-<gen>`（唯一、保留），再让 World 绑新路径；被 rename 的清单
  出现在启动 JSON 的 `retired_sockets` 里。非 socket 的占用一律 `REFUSING_TO_REPLACE_NON_SOCKET`
  拒绝启动。
* 等两个 socket 就绪（有超时；超时 → 打印 `CHILD_SERVICE_TIMEOUT`、`exit 4`，并把子进程
  日志路径一起打出来）。
* stdout 只输出那一行 JSON；此后日志走 `<root>/logs/serve.log`。
* 收到 `SIGTERM`/`SIGINT`：只停**自己起的** pid（SIGTERM → 有界等待），不 `rm` 别人的
  socket、不删数据、不动 WAL；socket 文件全部留在磁盘上。
* 子进程退出码与日志保留在 `<root>/logs/{supply,world}.log`。

## 4. HTTP 面

前缀 `/api/phone`，JSON only，不得有第二条路由。

### 4.1 安全闸门（全部 fail closed）

| 闸门 | 规则 | 失败 |
|---|---|---|
| 绑定 | 只监听 `127.0.0.1`，单实例 | 启动即拒绝 |
| `Host` | 必须 `127.0.0.1` / `localhost`，可带端口；其它（含 DNS rebinding 域名、`::1`）一律拒 | 403 `HOST_NOT_ALLOWED` |
| `Origin` | 若存在必须是 `http://<Host>` 同源 | 403 `ORIGIN_NOT_ALLOWED` |
| `Sec-Fetch-Site` | 若存在 ∈ {`same-origin`,`none`} | 403 `CROSS_SITE_REFUSED` |
| `X-CSRF-Token` | 所有 `POST` 必须等于 `GET /state` 的 `csrf_token`（常量时间比较） | 403 `CSRF_TOKEN_MISSING` / `CSRF_TOKEN_INVALID` |
| 方法 | 只用本文列出的方法 | 405 `METHOD_NOT_ALLOWED` |
| 请求体 | `Content-Type: application/json`、≤ 64 KiB、JSON 对象、**字段白名单**（多一个字段就拒） | 400 |
| 身份 | body 里的 `uid`/`authority`/`main_self_verified`/`home`/`price`/`balance`/`owner` **不是身份**：手机层直接丢弃并拒（`CLIENT_IDENTITY_REFUSED`）；uid/subject/home 只来自服务端实例 | 409 |
| 静态 | 只有 `/`（→`index.html`）、`/index.html`、`/app.js`、`/style.css` | 其它 404 |

`csrf_token` 每次 `serve` 随机生成（32 字节 hex），只在内存与 `/state` 响应里存在；
不写日志、不进 URL、不进聊天记录。

### 4.2 `GET /api/phone/state`

```json
{"ok": true, "read_only": false,
 "csrf_token": "<hex>",
 "instance": {"name": "...", "subject": "chiyo", "mode": "local_owner",
              "fingerprint": "phone:<sha256(profile_home)[:16]>"},
 "apps": {
   "notes":      {"status": "available", "data": {"item_count": 1}},
   "references": {"status": "available", "data": {"item_count": 0}},
   "reading":    {"status": "available", "data": {"item_count": 0}},
   "wallet":     {"status": "available", "data": {"currency": "JPY", "balance": 10000}},
   "shop":       {"status": "available", "data": {"catalog_count": 1, "purchase_available": true}},
   "inventory":  {"status": "available", "data": {"item_count": 0}},
   "body":       {"status": "unavailable", "reason_code": "BODY_VIEW_NOT_BOUND"}
 }}
```

* 每个 app **一定有** `status` ∈ `available|unavailable|disabled`；可带 `reason_code`；
  `data` 是**摘要**（`/state` 不复制整份钱包/目录）。不可用时 `data` 可为 `{}`。
* `read_only` 来自实例与运行状态（例如 Supply socket 不在 → notes/references/reading
  为 `unavailable`，但 `read_only` 仍是 `false`：只读是实例属性，不是缺依赖的挡箭牌）。
* 不在这里初始化经济、不造补助。

### 4.3 `GET /api/phone/notes`

```json
{"ok": true, "head": "<owner head version_id 或 null>",
 "items": [{"id": "memo:…", "title": "…", "body": "…", "revision": 1,
            "updated_at": "…", "observed_object_ref": null}],
 "artifact_id": "…", "private_text_included": true}
```

* 正文来自 `PrivateDailyItems.full_text(memo_id)`：**本地 owner 主动打开自己的笔记**，
  这是唯一包含正文的读法；`/state`、`/references`、`/reading` 都**不含正文**。
* `head` 是 owner 的真实 head version id，用于 `expected_head` 的 CAS 前提示。

### 4.4 `POST /api/phone/notes`

请求：`{"title": "…", "body": "…", "operation_key": "<uuid>", "expected_head": "<可选>", "note_id": "<可选>"}`

* `operation_key` 必填（UUID 或 ≤128 的 key），前端**失败重试用同一个 key**。
* `expected_head` 若给出且 ≠ 当前 head → `rejected / NOTE_HEAD_CONFLICT`，**不写**。
* **没有标题回退**：不传 `note_id` 时**永远新建**一条 memo（`memo_id` 由 `operation_key`
  派生：同一个 key 重放是 `already_applied`，不同 key 即使标题/正文完全相同也是**两条独立
  笔记**）。同名不会覆盖任何已存在的笔记。
* 传 `note_id` 才是修订：只改正文（owner 的 `revise_memo` 契约）。`note_id` 不存在 →
  `rejected / NOTE_NOT_FOUND`；不是 memo → `rejected / NOTE_ID_NOT_A_MEMO`；
  **标题与已存标题不同 → `rejected / NOTE_TITLE_CHANGE_NOT_SUPPORTED`**（改名不支持就
  明确拒绝，不会假装保存了新标题）。
* 响应：`{"ok": true, "status": "applied|already_applied", "data": {"id": "…",
  "operation_key": "…", "head": "…", "version_number": N}}`；owner 拒 →
  `ok:false, status:"rejected", reason_code:<owner reason>`。

### 4.5 `GET /api/phone/references`

```json
{"ok": true, "items": [{"id": "ref:…", "label": "…", "source_id": "…",
 "source_revision": "…", "read": false, "content_fetched": false, "saved_at": "…"}]}
```

### 4.6 `POST /api/phone/references`

请求：`{"source_id","source_revision","label","operation_key"}`。只存指针：
`read=false`、`content_fetched=false`，**不抓外网、不标已读**。
`receipt_ref` 由服务端写成 `operator:phone-reference:<operation_key>`——它记录的是
「operator 自己这条保存命令」，不是任何被取得的内容。

### 4.7 `GET /api/phone/reading`

```json
{"ok": true, "understanding": "UNKNOWN",
 "items": [{"id": "acq:…" | "pos:…", "kind": "acquisition" | "position",
            "source_id": "…", "source_revision": "…", "position": 3,
            "understanding": "UNKNOWN", "needs_reread": false, "at": "…", "label": ""}]}
```

只列**真实已取得**的来源与真实阅读位置；`understanding` 恒 `UNKNOWN`；不返回正文。

### 4.8 `GET /api/phone/wallet`

照抄 `PhoneShopping.wallet()`：`{"ok","status","reason_code"?,"data":{currency,balance,
transactions:[{transaction_id,kind,direction,amount,occurred_at,catalog_item_id?,
purchase_intent_id?,source?}],owner,starting_grant}}`。

### 4.9 `GET /api/phone/shop`

照抄 `PhoneShopping.shop()`：`data.store_id="phone_virtual_store"`、`data.catalog`
（`catalog_item_id/name/description/price/currency/usable_kind`）、`data.purchase_available`、
`data.availability`/`payment`/`delivery`。`purchase_available=false` 时带 `reason_code`。

### 4.10 `POST /api/phone/purchases`

请求：`{"catalog_item_id","operation_key"}`。直接调
`PhoneShopping.purchase(catalog_item_id, operation_key)`；`operation_key` 就是经济里的
`purchase_intent_id`。响应：
`{"ok","status":"applied|already_applied|rejected|unknown","reason_code"?,"data":
{transaction_id,item_instance_id,catalog_item_id,price,currency,current_balance,
occurred_at,ownership,delivery_location,materialization}}`。
`materialization` ∈ `materialized|already_materialized|pending`；`pending` 不是失败（钱与
产权已落，物化可重放同一 key 补完）。**结果 unknown 时不自动换 key 重发**，前端用
`GET /operations/<key>` 对账。

### 4.11 `GET /api/phone/inventory`

照抄 `PhoneShopping.inventory()`：`data.items[{item_instance_id,name,catalog_item_id,
ownership,location,delivery_status,usable,blocked_reason?,materialization,consumed,
receipt_id?}]`、`data.pending_count`。

### 4.12 `POST /api/phone/items/use`

请求：`{"item_instance_id","operation_key"}`。只经原 Resolver（`PhoneShopping.use`），手机
侧不改任何 state；`status:applied` 的 `data.receipt` 是 substrate 里那一条消耗回执。
重放同一 key → `already_applied`（不二次消耗）；换 key 再消耗 → `rejected/ALREADY_CONSUMED`。

### 4.13 `GET /api/phone/operations/<key>`

派生查询（无新 store）：命中购买流水 → 购买结果；命中 ledger → `SUCCESS`/`FAILURE`/
`UNKNOWN`（`UNKNOWN` 原样，不自动换 key 重发）；都没有 → `404`、
`{"ok":false,"status":"not_found","reason_code":"OPERATION_NOT_FOUND"}`。

## 5. HTTP 状态码与 JSON 语义

| 信封 `status` | HTTP | 含义 |
|---|---|---|
| `applied` / `already_applied` | 200 | owner 已提交（`already_applied` 幂等重放，不二次扣/不二次消耗） |
| `rejected` | 409 | 明确拒绝（`reason_code` 必带），**什么都没改** |
| `disabled` | 409 | 特性关（`FEATURE_DISABLED` 等） |
| `unavailable` | 503 | 依赖不可用（`WORLD_BODY_SERVICE_UNAVAILABLE`、`ECONOMY_STATE_ERROR`、Supply 不在） |
| `unknown` | 202 | 结果未知：**不许换 key 重发**，先 `GET /operations/<key>` |
| `not_found` | 404 | 操作/路由不存在 |

* 只有**一个**信封形状：`{"ok":bool,"status":str,"reason_code"?:str,"data"?:object,…}`。
* 读取类失败也用同一形状（`ok:false` + `503`/`404`），不返回 `null`。
* 400/403/405 用 `{"ok":false,"status":"rejected","reason_code":"<网关原因>"}`。
* 500 只用于本服务自身异常，`{"ok":false,"status":"error","reason_code":"PHONE_INTERNAL_ERROR"}`，
  **不回显**异常文本、路径、token。
* 永不预置假数据：没有服务/没有 socket → 如实 `unavailable`，界面显示未连接。

手机/工具自有的原因码：`NOTE_HEAD_CONFLICT`、`NOTE_NOT_FOUND`、`NOTE_ID_NOT_A_MEMO`、
`NOTE_TITLE_CHANGE_NOT_SUPPORTED`、`PHONE_ENTRY_NOT_AUTHORIZED`、
`PHONE_DELEGATED_SOURCE_REFUSED`、`PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`、
`PHONE_WRITE_DISABLED`、`PHONE_PROFILE_MISMATCH`、`REFUSING_TO_REPLACE_NON_SOCKET`。

## 6. `body` app

只读、可选：`GET /state` 里 `body` 为 `available` 时 `data` 只含真实字段
（`pose`、`location`、`world_revision`，来自 gateway `get_snapshot`）；没有 gateway →
`{"status":"unavailable","reason_code":"BODY_VIEW_NOT_BOUND"}`。手机不造生理指标、不回血。

## 7. 个体自己的手机：`chiyo_phone` 工具

* 工具名 `chiyo_phone`，参数 `{"action": "<enum>", …}`，`additionalProperties: false`。
  `action` ∈ `wallet|notes|references|reading|shop|inventory|operation|note_save|
  reference_save|purchase|use`。
* **入口授权 = `hermes_plugin.allowed` 同一规则**（不是另发明一套）：
  * `cli`：`cli_owner: true` 且 `local_owner_uid == os.getuid()`；
  * `local` 入口：**永远拒绝**；
  * 网关平台：只接受**已存在**的 DM 绑定（`gateway_bindings[platform]` 里含
    `conversation_key`，且 key 带 `:dm:`）；
  * **被委派的会话**（`parent_session_id` / `delegated`）**一律拒绝**。
  * 失败码：`rejected / PHONE_ENTRY_NOT_AUTHORIZED`、`rejected / PHONE_DELEGATED_SOURCE_REFUSED`。
  * `platform` / `conversation_key` 只来自宿主 handler kwargs；`args` 里没有任何入口字段，
    模型**不能**给自己发入口。
* **显式开关**：profile `chiyo/config.json` 需要
  `{"phone": {"enabled": true, "base_url": "http://127.0.0.1:<port>",
  "profile_home": "<instance root>", "write_enabled": false,
  "private_artifacts": false}}`。
  * 没有 `phone.enabled` → `disabled / PHONE_DISABLED`（默认禁）。
  * 读类只需要 `enabled`；**写类**（`note_save`/`reference_save`/`purchase`/`use`）还要求
    `write_enabled: true`，否则 `rejected / PHONE_WRITE_DISABLED`（默认禁写）。
  * `profile_home` 必须等于本进程 profile 里登记的手机实例根；不一致 → `rejected /
    PHONE_PROFILE_MISMATCH`（个体只碰自己的手机）。
* **私人档案是第二把钥匙**：`memory` 打开且 `memory_tool_policy != "unrestricted"` 时，
  本工具只保留 `wallet|shop|inventory|operation`；`notes|references|reading`（正文读取）
  与 `note_save|reference_save|purchase|use` 需要 profile 显式
  `phone.private_artifacts: true`，否则 `rejected / PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`。
  **开启手机不等于打开遗忘保护**；早先"工具不读 DB 所以不可能读到被遗忘历史"的说法不成立，
  已作废（私人 notes 同样可以保存她的过往）。
* 工具是**客户端**：走同一 `/api/phone/*`（先 `GET /state` 取 `csrf_token`，再带
  `X-CSRF-Token`），不直连 Supply/World，也不绕过上面的闸门。
* 来源分开：工具的 mutation 带 `X-Phone-Actor: model`，浏览器 UI 不带（默认 `user`）；
  手机把这条**声明**记在 `<root>/phone/operations.jsonl`（只追加）。经济/Resolver 侧的
  provenance 不变（`local_owner_phone*`），**不**伪造 `verified_main_self`、不伪造
  自主 Decision。
* 结果未知：工具返回 `status:"unknown"` 并给出 `operation_key`；再次调用必须先
  `action:"operation"` 对账，**不自动换 key 重发**。

## 8. 不做什么

无真实支付/充值/外发/平台账号；不新增经济/消耗/笔记存储；不改物理店规则；不把本地
owner 标成 main-self；不在手机侧直接改 substrate/economy；不自动启动 WorldBodyService；
不清理 256 上限；不把 profile/仓库根当静态目录；不跑真实模型、不 24h 常驻。

## 9. 世界/身体（v0.4 世界/身体轮追加，字段只按 C 的契约）

本节的最终字段契约是 `docs/engineering/v04-world-body-contract.md`（C）；这里只写 P 的路由、
闸门与转发规则，结果见 `docs/engineering/v04-world-api-result.md`。

* `GET /api/phone/world` → `PhoneWorld.snapshot()`，纯读（不初始化/不发钱/不结算到达/不在 GET
  上迁移），返回 C 的九块 `data`：`world/location/local_environment/destinations/travel/
  objects/body/actions/recent_executions`。
* `POST /api/phone/world/actions`，body 只允许 `{action, params, operation_key}`：
  `action ∈ {MOVE, PICK_UP, PLACE, POSE, REST}`；`params` 逐动词精确白名单（`MOVE`
  `destination_location_id`；`PICK_UP` `object_id`+`slot_id`；`PLACE` `object_id`+`target`；
  `POSE` `pose`[, `orientation`]；`REST` `state`[, `mode`]；`mode` 只随 `state=start`）。
  未知动词 400 `UNKNOWN_WORLD_ACTION`；多余/缺失/非法参数 400；params 或 `target` 里出现
  `uid`/`authority`/`home`/`subject`/`elapsed(_seconds)`/身体值 → 409
  `CLIENT_IDENTITY_REFUSED`。信封与 HTTP 码沿用 §5。
* `GET /api/phone/world/operations/<key>` → `PhoneWorld.operation(key)`；未知 key 404
  `OPERATION_NOT_FOUND`，结果未知 202，**只用原 key 对账**。
* `GET /api/phone/state` 的 `apps` 增加 `world`（`data` 是 C 快照的摘要：world_id/revision/
  location/travel/objects/body/verbs/九块状态），`apps.body` 升级为 C 的 `data.body` 投影
  （`pose/orientation/recovery/signals/signals_status/rest_evidence/bands_only/
  raw_values_exposed/body_status`），手机侧不算任何身体值、也不把 partial 说成 available。
* `--with-world` 的 `prepare-demo` 追加：worldgen 室内场景/清单、按世界包声明写能力、在**新建且
  pristine** 的根里由 substrate 自己的 writer 摆一次可拿物件与休息家具；`serve` 接受
  `--tick-seconds`（默认 5.0）转给 World 子进程（到达与恢复只在 owner tick 推进）。
* `chiyo_phone` 新增 `world`（读）/`world_action`（写，需 `phone.write_enabled`）/
  `world_operation`（对账）；参数形状 `{"action":"world_action","world_action":"MOVE",
  "params":{...},"operation_key":"…"}`，工具自己再校验一遍白名单与身份。
