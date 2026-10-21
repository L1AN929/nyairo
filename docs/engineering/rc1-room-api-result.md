# rc1 房间生活链（P 侧）：手机接线结果

写集：`components/phone/{service.py,server.py,demo.py}`、`scripts/phone.py`（`serve` 的新 flag）、
`chiyo_bundle/phone_tool.py`、新增 `tests/test_room_living_api.py` / `tests/test_room_living_tool.py` /
`tests/test_room_living_serve_flag.py`、本文件、[用户指南](rc1-room-userguide.md)、note
`.agents/notes/implemented/feature/2026-10-21-rc1-room-phone-wiring-and-counter-purchase.md`。
后续修正轮（§7.1）再碰了两处：`components/phone/service.py` 的 `_room_summary()`，以及**明确授权**
的一条旧断言 `tests/test_phone_world_api.py:242`（动作清单从写死五项改成精确等于 C 的 `VERBS`）。
再一轮（§7.2）只碰开关通路：`demo.py` 的 `_world_child()`/`serve_instance()`/`effective_features()`、
`service.py` 的 `PhoneService(feature_overrides=...)`、`scripts/phone.py serve --with-room-living`，
以及新增的纯配置构造测试。
没有改动 `components/world/**`、`components/phone/web/**`（U 另行对齐）、`MANIFEST.json`；
没有 stage/commit/push，没有真实模型、真实支付、生产开关。

## 1. 结论

P 这一层已经接好三条缝，但**没有**在真实世界实例上跑过端到端链（C 的核心还在写）：

| 缝 | 状态 |
|---|---|
| `GET /api/phone/store` 纯读货架/营业/预算 | 已接，桩测 4 项 |
| `POST /api/phone/store/purchases` 柜台购买 | 已接，桩测 6 项 |
| `/state` 的实体店摘要 + 房间摘要（到货/自有家具/承载面/灯） | 已接，桩测 4 项（含 2 项真实嵌套形状，见 §7.1） |
| 世界动词 `ARRANGE` / `TOGGLE_FIXTURE` 转发 | 已接，桩测 7 + 工具 6 项 |
| `chiyo_phone` 工具 `store` / `store_purchase` | 已接，工具测 5 项 |
| 与 C 真实 facade 的签名/动词表一致性 | 已接，`FacadeConformanceTest` 2 项（真 import，无实例） |
| 真实经济/交付/placement/灯语义 | **C 的**（P 只透传，不冒充） |

## 2. P 消费的 C 接口（精确）

P 只按下面这些调用与字段工作，别的一概不碰；`PhoneShopping`/`PhoneWorld` 由 C 提供：

```python
PhoneShopping.store() -> dict                      # 信封 {ok,status,data{...}}
PhoneShopping.store_purchase(catalog_item_id: str, operation_key: str) -> dict
PhoneWorld.snapshot()  # data 里可选新增 deliveries / owned_furniture / surfaces /
                       # local_environment.light（结构由 C 冻结）
PhoneWorld.act(action, params, operation_key)       # action ∈ {ARRANGE, TOGGLE_FIXTURE, ...}
```

`store()` 的 `data` 里 P 会读/原样透传的字段（其余字段原样保留、不解释）：
`store_id`、`channel`、`availability`、`currency`、`catalog[]`、`purchase_available`；
`/state` 只额外抽 `catalog` 长度、`store_id`、`channel`、`availability`。

房间动词的键集已按 C 的 `PhoneWorld.VERBS` / `PARAM_SPEC` **逐字对齐**（不是猜的超集），
`FacadeConformanceTest` 会直接 import C 的模块比对，两边任何一侧改名都会立刻红：

| 动词 | P 允许/要求的键 | P 自己就拒 |
|---|---|---|
| `ARRANGE` | `object_id` + `target`（都必填） | `target.kind ∉ {room,surface}`、任何黑名单键、多余键 |
| `TOGGLE_FIXTURE` | `object_id` + `state`（都必填） | `state ∉ {on,off}`、任何黑名单键、多余键 |

C 没有 `RELOCATE` 这个别名：P 不接受、也不广告（发它 = `UNKNOWN_WORLD_ACTION`）。

黑名单（409 `CLIENT_IDENTITY_REFUSED:<keys>`）：`uid/euid/gid/authority/…/home/hermes_home/
world_home/profile/owner/subject/binding`、`elapsed*/duration_seconds/seconds/rest_seconds`、
`price/price_jpy/amount/cost/balance/currency/delivery_state/delivery_seconds/elapsed_hours`、
`fatigue/activation/comfort/sensory_load/tension/warmth/health/energy/body_values/bands/signals`。

## 3. HTTP 契约（P 面）

| 方法 | 路径 | 请求体 | 闸门 |
|---|---|---|---|
| GET | `/api/phone/store` | — | Host/Origin/Sec-Fetch-Site（与 `/world` 同）；零写 |
| POST | `/api/phone/store/purchases` | `{catalog_item_id, operation_key}` | CSRF + JSON + 64KB + 身份白名单 |

状态码沿用既有映射：`applied`/`already_applied` → 200，`rejected`/`disabled` → 409，
`unavailable` → 503，`unknown` → 202，`not_found` → 404。P 从不把拒绝升格成成功，
也从不改写 C 的 `reason_code`。每次 POST 往 `<root>/phone/operations.jsonl` 追加一行
（`action=store_purchase`，`actor` 取 `X-Phone-Actor` 声明，缺省 `user`）。

`GET /store` 在 C 的 facade 还没有 `store` 方法时返回
`503 / unavailable / PHONE_FACADE_UNIMPLEMENTED {method:"store"}`——「未实现」不是「空的店」。

## 4. `/state` 新增摘要

- `apps.store`：`{status, data{catalog_count, store_id, channel?, availability{...}, app}}`；
  虚拟店 `apps.shop` 一字未动（`test_phone_api.py` 的 `catalog_count == 1` 仍成立）。
- `apps.world.data.room` 只从 **C 真正发布的位置**读：`objects.deliveries` /
  `objects.owned_furniture` / `objects.surfaces` 各给 `{status, reason_code?, count?}`；`count` 只在
  C 真的发布了列表时出现（`items`/`entries`/`rows`/`records` 或恰好一个列表），否则不给数。
  `light` 是 `data.local_environment.light` 的逐字段复制。缺块 → `{status: null,
  reason_code: "NOT_REPORTED"}`：**未知灯不是「关」，未知配送不是「没有」**。顶层不存在这四个块
  的兼容读（那是假形状，见 §7.1）。
- `apps.world.data.blocks` 仍是原来那 9 块（未增未减），旧断言不受影响。

## 5. 工具面

`chiyo_phone` 新增 `action=store`（只读）与 `action=store_purchase`（写）。`store_purchase`
与既有 `purchase` 同属 `PRIVATE_ARTIFACT_ACTIONS`：受限记忆 + 无 `phone.private_artifacts` 时
一起拒。入口规则原样：`cli` 需 `cli_owner`+真实 uid、网关只认已存在的 DM 绑定、`local` 与被委派
会话一律拒、实例指纹不符 `PHONE_PROFILE_MISMATCH`。`store_purchase` 的 `operation_key` 缺省时
按「动作+参数」派生（同一意图同一 key，不换 key 重试）；`world_action` 的 `state` 分动词钉死
（REST 只 `start/stop`，灯只 `on/off`）。

## 6. 开关与在旧 root 上启用（不重写旧实例）

**先纠正上一版这个位置写错的一句话。** 上一版说「`serve` 进程的环境变量会被 `_child_environment`
继承给 world child，也会进 `PhoneService.environment`」，暗示「设个环境变量就自然两端生效」。实际
不是：

1. `PhoneService.environment` 是 `os.environ` **再被 `instance.features` 覆盖**（features 优先），
   旧实例的 `features` 里记着 `CHIYO_WORLD_DAILY_RHYTHM_ENABLED=0`，所以外部 env 永远输；
2. `demo._world_child()` 曾把这个 flag **硬编码成 `0`** 写进子进程环境，连继承都谈不上；
3. 结果：手机面认为「营业规则关闭」（实体店 `WORLD_FEATURE_REQUIRED`/`DAILY_RHYTHM_*`），子进程
   也认为关闭，而 `--with-world` 之外的营业判定 `/api/phone/store` 永远不可用。两份真相各自
   「诚实」，但用户开着 flag 也打不开——这就是本轮补的缺口。

现在的做法：`scripts/phone.py serve --with-room-living` 是**唯一**显式开关。它把同一组
「有效开关」（`demo.effective_features()`：实例记录的 `features` + 本次覆盖）交给两端——
world child 的环境变量和 `PhoneService(feature_overrides=...)`——并且**只在本进程内存里生效**：

* `instance.json` 的 `features` 一个字节都不改（`serve` 仍只改写 `supply_socket` /
  `supply_generation` 两行，属既有行为）；
* 不带 `--with-room-living` 时行为与旧启动完全一致（daily rhythm 仍是记录值 `0`，房间灯不显式开）；
* 覆盖只碰两个开关：`CHIYO_WORLD_DAILY_RHYTHM_ENABLED`、`CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED`。
  价格、营业时段、时区、`temporal_home_delivery` 的 7200s / instant 模式都不在这里，也不改。

在同一 root 上带 flag 重启（C 的契约落定后，开关名以 `rc1-room-living-contract.md` 为准）：

```bash
cd /mnt/d/nyairo-rc1-body-backend
# 0) 先只读核对「这个 PID 确实是这个实例的 serve」，再停它（不要批量 kill）
pgrep -af 'scripts/phone.py serve' ; ls -l /proc/<PID>/cwd
# 1) 备份：只用于取证 / 受控恢复，**不**自动执行还原（不删任何文件；备份名带时间戳）
cd /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094
STAMP=$(date +%Y%m%d-%H%M%S)
cp -a instance.json instance.json-backup-$STAMP
cp -a one-day/world-home/state/world_property_economy.json \
      one-day/world-home/state/world_property_economy.json-backup-$STAMP
cp -a one-day/world-home/data/world/body_substrate.json \
      one-day/world-home/data/world/body_substrate.json-backup-$STAMP
cp -a one-day/world-home/data/world/world_state.json \
      one-day/world-home/data/world/world_state.json-backup-$STAMP
# 2) 同根重启（前台；Ctrl+C 停，等 owned children 退出）
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 --port 0 --tick-seconds 5 \
  --with-room-living
```

注意事项（都有既有先例）：

- **不要** `prepare-demo` 已有根（会 `ROOT_NOT_EMPTY` 拒绝；绕过它等于重置，禁止）。
- 重启会改写 `instance.json` 的 `supply_socket`/`supply_generation` 两行（既有行为，为了不
  删除/不抢占旧 socket）；旧 socket 文件保留，新 generation 用新名字。
- 旧 `gateway.sock` / `control.sock` 会被 `retire_socket` **改名**保留，不会被删除。
- 余额与已有物件原地承接（当前 9760 JPY、一笔交易、卧室里那件原点心），不重买、不重置。
- 冲突即停：若启动报 `SUBSTRATE_NOT_PRISTINE` / `LOCAL_OWNER_UID_MISMATCH` /
  `CHILD_SERVICE_TIMEOUT`，停下看日志，**不要**手工删状态往前推。
- **功能停用 = 正常停机 + 同根重启，不是数据回滚**：核验「这个 PID 确实是这个实例的 serve」
  后正常停止，**不带 `--with-room-living`** 用同一 root 重启即可（不带 flag = 房间链关闭，与旧
  启动一致；`features` 也不会因为这一轮被改过）。已经发生的交易、产权、摆放、灯状态、
  执行账本全部保留，不覆盖任何数据档；schema 兼容性不确定时先只读报告阻塞，不自动拿旧代码回写。
- **备份只用于取证 / 受控恢复**：本轮之后同一实例会有新交易、新摆放、新的开灯记录，把
  `world_property_economy.json` / `body_substrate.json` / `world_state.json` / `instance.json`
  无条件覆盖回去会让余额、产权与历史一起倒退。真要恢复，必须先核对当前 ledger（含未决动作与
  删除屏障）以及之后的全部历史，再决定是否、恢复到哪一刻；上面那组 `cp` 命令可以预先准备，
  但**不要**自动执行还原。
- 一句话：**数据回滚 ≠ 功能回退**。上一轮那种「拿旧备份盖回去」的做法在本轮明确不作数。

## 7. 验证（实际命令与真实 exit）

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_api.py          # Ran 23 tests ... OK  (exit 0，见 §7.1)
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_tool.py         # Ran 16 tests ... OK  (exit 0)
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_serve_flag.py   # Ran 13 tests ... OK  (exit 0，见 §7.2)
```

前两个文件是真 loopback HTTP（`create_server`）+ 真 `PhoneService` + **桩** facade + 真
`chiyo/config.json`；第三个是纯配置构造（不起子进程、不绑 HTTP、不 prepare），夹具目录都建在系统
临时目录下并保留（不删），都不需要真实世界实例。

覆盖的正面：货架透传、柜台购买透传（成对 id+key）、`/state` 摘要、`ARRANGE`/`TOGGLE_FIXTURE`
透传、灯绝对开关、工具的两个新动作与 schema，以及 2 项**对 C 真实 facade 的签名/动词表一致性**
（只 import，不起实例、不连 socket）。
覆盖的负面：Host/Origin/Sec-Fetch-Site、CSRF 缺失/错误、非 JSON Content-Type、身份/价格/余额/
elapsed/身体值（409）、未知字段（400，含 `item_instance_id`/`RELOCATE`）、缺
`operation_key`/`catalog_item_id`（400）、facade 未实现（503）、`target.kind=container`（400）、
`state=toggle/ON/true`（400）、缺 `object_id`/`target`（400）、未知动词（400）、
`write_enabled=false` → `PHONE_WRITE_DISABLED`、被委派会话
`PHONE_DELEGATED_SOURCE_REFUSED`、未绑定入口 `PHONE_ENTRY_NOT_AUTHORIZED`、受限记忆
`PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`（与老 `purchase` 一致）、GET 前后整棵夹具目录零变化。

### 7.1 房间块 nested 消费：原红 → 改后绿（本轮修正）

原红（先改测试形状、还没改代码时；桩快照改成 C 的真实嵌套 + 新增 2 条真形状用例）：

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_api.py
# Ran 23 tests ... FAILED (failures=3)     exit 1
#   test_real_nested_owner_blocks_are_read_where_the_owner_publishes_them:
#     AssertionError: 'NOT_REPORTED' == 'NOT_REPORTED' : deliveries
```

根因：`PhoneService._room_summary()` 按**顶层**键读 `data.deliveries` / `data.owned_furniture` /
`data.surfaces`，而 C 的快照把它们挂在 `data.objects.*`（灯在 `data.local_environment.light`，那条
原本就是对的）。所以 owner 明明发布了三块，`/state` 一律报 `NOT_REPORTED`——「已读但读错位置」，
不是 C 没发布。修法只有一处：读 `objects` 里的那三块；不新增顶层兼容读、不造第二形状。

改后绿（同一套夹具，如下命令的全部实际 exit）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_api.py                       # Ran 23 ... OK  exit 0
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_api.py StateRoomSummaryTest   # Ran 4 ... OK  exit 0
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_api.py FacadeConformanceTest  # Ran 2 ... OK  exit 0
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_phone_world_api.py \
    PhoneWorldStateTest.test_world_snapshot_is_complete_and_writes_nothing             # Ran 1 ... OK  exit 0
```

`tests/test_phone_world_api.py:242` 那条写死五项 `local_owner_verbs` 的断言原本是红的
（`First list contains 2 additional elements ... 'ARRANGE'`，单跑 exit 1）。按明确授权只改这一条：
现在精确等于 C 的 `world_phone_world.VERBS`，并要求原来那五个动词仍在表里（不是宽泛 `contains`）；
同文件其余安全断言（九块、两读零写、bed `takeable=false`、浮点扫描）一字未动。

### 7.2 `serve --with-room-living`：缺口 → 收敛（本轮修正）

缺口（改前，读代码 + 探测实测）：`demo._world_child()` 把 `CHIYO_WORLD_DAILY_RHYTHM_ENABLED`
写成字面量 `"0"`（`_child_environment()` 是 `os.environ` 再被它覆盖，所以外部 env 进不去），
而手机面 `PhoneService.environment` 是 `os.environ` **再被 `instance.features` 覆盖**，旧实例里
记着 `0`。两边都「诚实」，但用户把 flag 设成 `1` 也打不开实体店。探测（`env=1`、不带 flag）：

```text
{"case": "env=1, no flag (gap: store stays closed)",
 "phone_service_env_daily": "0", "world_child_env_daily": "0",
 "phone_service_env_room": null, "world_child_env_room": null}
```

改后（同一探测脚本，`PROBE_EXIT=0`）：

```text
{"case": "env=1, --with-room-living (both sides on)",
 "phone_service_env_daily": "1", "world_child_env_daily": "1",
 "phone_service_env_room": "1", "world_child_env_room": "1"}
{"case": "env=0, --with-room-living (flag wins)",
 "phone_service_env_daily": "1", "world_child_env_daily": "1",
 "phone_service_env_room": "1", "world_child_env_room": "1"}
```

定向测试（`tests/test_room_living_serve_flag.py`，纯配置构造，不起子进程/不绑 HTTP）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_serve_flag.py   # Ran 13 tests ... OK  exit 0
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_api.py          # Ran 23 tests ... OK  exit 0
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_room_living_tool.py         # Ran 16 tests ... OK  exit 0
```

13 项覆盖：默认（不带 flag）有效开关 == 实例记录值且**不多出一个键**；带 flag 只多开那两个开关
（并断言没有 `PRICE`/`HOUR`/`TIMEZONE`/`TZ`/`DELIVERY_MODE`/`DURATION` 这类键被顺手造出来）；
child 环境与有效开关逐键一致（含「不带 flag 时 room flag 仍继承外部 env = 旧行为」）；`PhoneService`
默认仍是 features 压过 env（旧行为），给 `feature_overrides` 时覆盖胜出且两端取值相同；一次
`serve_instance(room_living=True)` 之后 `instance.json` 的 `features` 与运行前**逐字节相同**，
只改了 `supply_socket`/`supply_generation`（socket 绑定仍照旧写）；`serve --help` 真的接受
`--with-room-living`。

**没有跑的**（诚实列出）：旧手机 45 项 / 世界 25 项等长集成套件（C 正在写核心，按纪律不反复扫
中间态）；真实世界实例的任何端到端链；任何浏览器/UI 验收（那是 U 的写集，本轮没有真实 browser
操作，故不声称页面可用）；真实支付、真实模型、生产开关、全库 SHA。

## 8. 边界与未做

- P 不配送、不物化、不迁移、不发钱、不算价格/余额/交付/身体值；GET 永远零写。
- 灯/书的「使用」语义、容器摆放、家具移动的能力判定都不在 P：没有 owner 就不给按钮。
- 未改 `components/world/**`（含 `world_phone_shopping.py` / `world_phone_world.py`）——这些
  是 C 的写集；P 只按上面第 2 节的签名消费。
- 未改 `components/phone/web/**`（U 的写集）、旧 tests、`MANIFEST.json`、rc1 移动引导/插件注册。
- 未 stage/commit/push/publish；没有安装包、没有生产切换。

本轮（形状修正）另外确认的边界：只改了 `components/phone/service.py` 的 `_room_summary()` 消费路径与
`tests/test_room_living_api.py`（桩改成真实嵌套 + 2 项新用例），以及明确授权的那一条
`tests/test_phone_world_api.py:242` 断言。没有改 `components/world/**`（含 C 的
`world_phone_world.py`）、没有改 `components/phone/web/**`（U 的写集，另行对齐）、没有改
`server.py` / `chiyo_bundle/phone_tool.py` / 旧 tests / `MANIFEST.json`，没有物理删除任何夹具或
备份，没有 `python -m py_compile`，没有碰 running 实例（未重启、未对 live 数据做任何写）。

开关轮（§7.2）的边界：**没有**把 `--with-room-living` 写进 `instance.json` 的 `features`（旧 root
记录什么还是什么，只在本进程内存与子进程环境里生效）；没有改价格、营业时段、时区、
`temporal_home_delivery` 的 7200s / instant 模式；没有改 `prepare_demo`（新根的 `features` 仍记
daily rhythm `0`）；没有碰 live 实例、没有重启任何服务、没有 `prepare-demo` 任何已有根（父级要
承接的 9760 JPY 与那件原点心原地不动，未用新 root 替代）。没碰 `components/world/**`、
`components/phone/web/**`、`chiyo_bundle/**`、`MANIFEST.json`；无物理删除、无 `py_compile`、
无 stage/commit/push。
