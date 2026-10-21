# v0.4 手机 API 交付结果（P 写集）

> 契约：`docs/engineering/v04-phone-api-contract.md`；World 侧唯一契约：
> `docs/engineering/v04-phone-shopping-contract.md`。
> 本文只记录**实际跑出来的事实**：命令、退出码、实际端口、实例路径、真实落盘证据。

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `components/phone/service.py` | 一个实例的读写面：笔记/收藏/阅读走 Supply `PrivateDailyItems`；钱包/商店/物品/购买/使用/操作对账走 W 的 `PhoneShopping`；`body` 只读投影；`operations.jsonl` 记操作来源 |
| `components/phone/server.py` | loopback-only HTTP：一个 JSON 信封、Host/Origin/Sec-Fetch-Site/CSRF 闸门、字段白名单、静态三文件白名单、状态码映射 |
| `components/phone/demo.py` | `prepare_demo`（一次真实实例 + 真 genesis + 真世界声明）、`serve_instance`（自己起 Supply/WorldBodyService 子进程，退出只停自己的 pid） |
| `components/phone/__init__.py` / `__main__.py` | 包入口；`python -m components.phone` == `scripts/phone.py` |
| `scripts/phone.py` | `prepare-demo` / `serve` / 两个内部 owner 子进程入口（`owner-supply`、`owner-world`） |
| `chiyo_bundle/phone_tool.py` | `chiyo_phone` 工具（profile 显式开关、loopback 客户端、写默认禁、operation 对账、`X-Phone-Actor: model`） |
| `chiyo_bundle/hermes_plugin.py` | `memory_tool_gate` 里只放行这个**不能读记忆**的手机工具（其余规则不变） |
| `plugins/chiyo/__init__.py` | 按 `world_move` 同样的窄模式注册 `chiyo_phone`（失败只记 warning，不影响插件） |
| `tests/test_phone_api.py` | 11 个真实栈正负（API 面） |
| `tests/test_phone_tool.py` | 8 个确定性 handler 正负（真落 Supply / 经济 / Resolver） |
| `docs/USER_GUIDE.zh-CN.md` §18 | 用户视角：怎么准备、怎么开、怎么用、东西坏了看哪里 |

## 2. 实际跑出来的命令与结果（WSL，`PYTHONDONTWRITEBYTECODE=1`）

```
$ python3 scripts/phone.py prepare-demo --root /tmp/nyaw05r.jdK3q/v04-phone-p-final1
exit 0
{"ok": true, "genesis": {"status": "created", "reason_code": "MOVE_IN_GRANT_CREATED",
 "starting_grant": 10000, "revision": 1, "provenance": {"source": "owner_approved_world_genesis",
 "kind": "move_in_grant"}},
 "world_id": "coastal_town_929711",
 "root": "/tmp/nyaw05r.jdK3q/v04-phone-p-final1",
 "supply_socket": "…/v04-phone-p-final1/run/supply.sock",
 "economy_state": "…/one-day/world-home/state/world_property_economy.json",
 "private_items_artifact_id": "artifact:…", "workspace_id": "workspace:…"}
```

```
$ python3 scripts/phone.py serve --instance /tmp/nyaw05r.jdK3q/v04-phone-p-final1 --port 0
{"ok": true, "url": "http://127.0.0.1:60537/", "port": 60537,
 "instance": "/tmp/nyaw05r.jdK3q/v04-phone-p-final1", "pid": 10069,
 "children": {"supply": 10070, "world": 10071}}
```

同一次 serve 会话里的真实接线（HTTP 200 / 200 / 200）：

* `GET /api/phone/state` → 七个 app 全部 `available`，`csrf_token` 64 hex，
  `instance.fingerprint = phone:<sha256(profile_home)[:16]>`；
* `POST /api/phone/purchases {virtual_snack_wafer, buy-1}` →
  `status=applied`，`price=240`，`current_balance=9760`，`ownership=OWNED`，
  `delivery_location=home/bedroom`，`materialization=materialized`；
* `POST /api/phone/items/use {item-…, use-1}` →
  `status=applied`，`execution_id=phone-exec:8ec3d207…`，
  `local_owner={source: local_owner_phone, uid: 0, binding:{subject: chiyo, world_home: …}}`，
  `receipt.receipt_id=world-consumption:…`，`applied_to_body=true`；
* 同一 key 重放 → `already_applied` 且余额仍是 9760、receipt 回执同一条；
* `GET /api/phone/operations/buy-1` → `applied`；`GET /api/phone/operations/nope` → 404
  `OPERATION_NOT_FOUND`；
* `SIGTERM` → `{"ok": true, "status": "stopped"}`，`exit 0`。

**同根重启**（新进程、新端口、同一 `/tmp/nyaw05r.jdK3q/v04-phone-p-final1`）：

```
restart_port 33711
restart_balance 9760 transactions 1
restart_items [('item-9b4b2a1049330a0735c85c0d11f91bbbf1fd6cb53c610bd48d7a191cbd3a335f',
               'CONSUMED', True)]
restart_notes 1
restart_exit 0 {"ok": true, "status": "stopped"}
```

## 3. 测试

```
$ python3 -m unittest tests.test_phone_api -v
Ran 11 tests in 203.712s
OK
$ python3 -m unittest tests.test_phone_tool -v
Ran 8 tests in 26.607s
OK
```

覆盖的正负（全部真实 owner，无 mock）：

* 状态/钱包/商店/物品来自 owner 自身投影（余额用 owner 文档 `starting_grant` + 真实流水
  重算，与 API 给出的数字一致）；
* 静态面：`/`、`/index.html`、`/app.js`、`/style.css` 仅此四个；`/AGENTS.md`、`/instance.json`、
  `/../AGENTS.md`、`/%2e%2e/instance.json`、`/one-day/instance.json` 全部 404；
* `Host: evil.example` → 403 `HOST_NOT_ALLOWED`；跨源 `Origin` → 403 `ORIGIN_NOT_ALLOWED`；
  `Sec-Fetch-Site: cross-site` → 403 `CROSS_SITE_REFUSED`；无/错 CSRF → 403（且笔记没有落地）；
* body 里的 `uid`/`main_self_verified`/`price`/`balance`/`authority`/`home` → 409
  `CLIENT_IDENTITY_REFUSED:*`；多余字段 → 400 `UNKNOWN_FIELD:*`；
* 笔记：写入 → 读回正文 → 同 key 重放 `already_applied`（head 不变）→ 同标题新正文新 key
  真的修订（revision≥2）→ 陈旧 `expected_head` → 409 `NOTE_HEAD_CONFLICT`（不写）；
* 收藏只存指针（`read=false`、`content_fetched=false`）；阅读 `understanding` 恒 `UNKNOWN`；
* 购买：扣款一次 + 物化 `room:home/bedroom`，重放不二次扣款，`operations/<key>` 可对账；
  未知目录 → 409（`UNKNOWN_CATALOG_ITEM` / `ITEM_NOT_SOLD_IN_PHONE_STORE`）；
* 使用：经 Resolver 落 `world-consumption:*` 回执，重放 `already_applied`，换 key →
  409 `ALREADY_CONSUMED`，物品变 `CONSUMED/usable=false`；
* Supply 不在 → `unavailable / SUPPLY_UNAVAILABLE`（不是空列表）；
* 工具：默认 `PHONE_DISABLED`；写默认 `PHONE_WRITE_DISABLED`；非 loopback 地址拒绝；
  别人的手机根 → `PHONE_PROFILE_MISMATCH`；购买/使用在**经济文档**与**substrate
  consumptions** 里都能查到；操作日志里这些 mutation 的 `actor=model`。

## 4. 已知边界 / 交接给 W 的一条观察

* **`inventory` 对已消耗物品报 `materialization:"pending"`**（同时 `delivery_status` 是
  `CONSUMED`、`consumed=true`、`usable=false`）。原因在 W 的
  `components/world/scripts/world_phone_shopping.py`：`placement is None` 时一律标
  `MATERIALIZATION_PENDING`，而消耗成功会移除 placement。手机侧按“照抄 facade”的契约**没有**
  改写这个字段；建议 W 在已消耗分支直接给 `materialization:"materialized"`（或新增
  `consumed` 优先判断）。本文件只报告，不代改 W 的写集。
* demo 以当前 uid（本机是 root）运行 Supply owner，因此显式设
  `LIFE_SUPPLY_REQUIRE_NON_ROOT=OFF`；多用户/生产部署必须保持该开关 ON 并使用专用 UID。
* 未做：真实浏览器 E2E（父级做一次体验链）；`body` 的更多字段；笔记删除（不在本版契约）。

## 5. 返修（父级 P0 验收发现，2026-10-17 第二轮）

### 5.1 更正历史结论（不藏）

* 上一版本文 §3 写"exit 只停自己的 pid、socket 都留着"，这一点对**数据**成立，但对
  **socket 文件**当时**不准确**：`scripts/phone.py owner-supply` 曾调用 owner 的
  `prepare_socket_path()`，它在"证明是 stale"时会 **`unlink`** 旧 socket；停服时也再调一次。
  同时 `WorldBodyService._bind_socket` 对已存在的 gateway/control 路径也会 unlink（W 的
  底层行为，本轮不改）。**上一版"未 unlink"的说法作废。**
* 现在（本轮）：删掉 `prepare_socket_path` 调用；每次 `serve`/`prepare` 用**新 generation 的
  新 socket 路径**；World 的 `gateway.sock`/`control.sock` 若已存在，由 P 的 wrapper 在启动
  子进程前 **rename** 成 `<name>.retired-<gen>`（唯一、保留）。P 不再调用任何会删 socket 的
  函数；非 socket 占用一律 `REFUSING_TO_REPLACE_NON_SOCKET` 拒绝启动。

### 5.2 笔记语义修正（真实缺陷）

* `components/phone/service.py::create_note` 不再有"同标题唯一就自动修订"的回退：不传
  `note_id` **永远新建**，`memo_id` 由 `operation_key` 派生（同 key 重放 `already_applied`，
  不同 key 即使同名同文也是**两条独立笔记**）。同名新建不会覆盖任何旧笔记。
* 修订只认 `note_id`；`revise_memo` 只改正文，因此**改标题会被明确拒绝**
  （`NOTE_TITLE_CHANGE_NOT_SUPPORTED`），不会出现"API 说新标题保存了、实际没改"。
  未知 id → `NOTE_NOT_FOUND`；不是 memo → `NOTE_ID_NOT_A_MEMO`。原 CAS（`expected_head`）
  语义不变。

### 5.3 工具入口与遗忘保护修正

* `chiyo_bundle/phone_tool.py` 现在先过**与 `hermes_plugin.allowed` 同一规则**的入口授权：
  CLI 需 `cli_owner: true` + `local_owner_uid == os.getuid()`；`local` 入口永远拒绝；网关只
  认已存在的 DM 绑定；被委派会话（`parent_session_id`/`delegated`）一律
  `PHONE_DELEGATED_SOURCE_REFUSED`。入口参数只来自宿主 kwargs，`args` 里没有入口字段。
* 私人档案（notes/references/reading 与 note_save/reference_save/purchase/use）在
  `memory` 打开且策略非 `unrestricted` 时，需要 profile 显式
  `phone.private_artifacts: true`，否则 `PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`；仅
  wallet/shop/inventory/operation 保持可用。**开启手机不再等于绕过遗忘保护**，上一版
  "不读 DB 所以不可能读到被遗忘历史"的断言作废（私人 notes 同样能保存她的过往）。
* `hermes_plugin.memory_tool_gate` 的注释同步更正：那一行 allowlist 只是让经济视图不被
  一刀切拦掉，真正收紧在工具自己的门上。

### 5.4 本轮定向验证（真实日志，returncode 直接记录，未用管道洗 rc）

```
$ python3 scripts/phone.py prepare-demo --root /tmp/nyaw05r.jdK3q/v04-phone-p-accept1
PREPARE_RC 0

$ python3 -m unittest tests.test_phone_api.PhoneAPINotesTest -v
RC=0        (Ran 5 tests ... OK；含新增：两同名独立创建 / 只改指定 id + 不改名 / 原 CAS 冲突)

$ python3 -m unittest tests.test_phone_tool -v
RC=0        (Ran 11 tests ... OK；含新增：入口授权正负 / 与 hermes_plugin.allowed 逐例等价 /
             受限记忆下私人档案需要第二把钥匙)

$ serve（accept1）第一次
SERVE1_ANNOUNCE port 53335  supply_generation serve-1792252018-10642  exit 0
SERVE1 apps 全 available，balance 10000 JPY，notes 0

$ serve 同根重启（accept1）
SERVE2_ANNOUNCE port 50763  supply_generation serve-1792252039-10659  exit 0
SERVE2_ITEMS []  SERVE2_NOTES 0（数据未重置）
RUN_FILES        ['phone.serve.json', 'supply-prepare-….sock',
                  'supply-serve-1792252018-10642.sock', 'supply-serve-1792252039-10659.sock']
WORLD_RUN_FILES  ['authority.json', 'control.sock', 'control.sock.retired-serve-1792252039-10659',
                  'gateway.sock', 'gateway.sock.retired-serve-1792252039-10659',
                  'world_body_authority.lock']
retired_sockets  gateway.sock.retired-… + control.sock.retired-…（rename，保留）
```

另一次全新根 `/tmp/nyaw05r.jdK3q/v04-phone-p-accept2` 上重跑了完整买卖链（新代码）：

```
SMOKE_RC 0
port 49245  purchase 200 current_balance 9760  replay 200 already_applied(9760)
use 200 applied（phone-exec:8ec3d207…，world-consumption:*）replay already_applied
inventory 该物 CONSUMED/consumed=true  serve exit 0
```

### 5.5 交给父级浏览器验收的根（已停止，可随时启动）

```
实例根：/tmp/nyaw05r.jdK3q/v04-phone-p-accept1
状态：已停止（无 serve、无子进程；10000 JPY、0 笔记、0 物品的全新链）
启动：
  cd /mnt/d/nyairo-v04-backend
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
      --instance /tmp/nyaw05r.jdK3q/v04-phone-p-accept1 --port 0
浏览器打开 stdout 第一行里的 http://127.0.0.1:<port>/
```
