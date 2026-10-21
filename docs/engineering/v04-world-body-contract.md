# v0.4 世界与身体契约（C 核心 owner：字段 / 命令 / REST 参数）

Status: **frozen for this round**（先写定，同轮实现；实测事实见 `v04-world-body-result.md`）
基线：`v0.3.0-rc1` = `41e818faaa24115fbf43db2977b15585c04144af`（工作树 `work/rc1-phone-world-body`）
Owner：C 写集 `components/world/scripts/**`、`components/world/service/**`
消费者：P（`components/phone/**` 非 web + `scripts/phone.py` + `chiyo_bundle/phone_tool.py`）、U（`components/phone/web/**`）

实现文件（唯一）：

| 角色 | 文件 |
|---|---|
| 手机侧唯一门面 | `components/world/scripts/world_phone_world.py`（`PhoneWorld`） |
| 唯一提交权威 | `components/world/scripts/world_action_resolver.py`（`REST` 加入 `ACTIONS`） |
| 唯一身体输入适配 | `components/world/scripts/body_consequence.py`（真实移动秒数、REST start/stop、到达延迟结算） |
| 唯一 owner tick / 到达写者 | `components/world/service/world_body_service.py`（`tick()` 里 `realize_due_travel` + 负荷结算） |
| 唯一本地 owner 命令 | `components/world/service/world_body_gateway_api.py`（`submit_local_owner_action`） |

本文件只写**当前代码里真的存在**的字段。凡标 `unavailable`/`unknown` 的地方，P/U 原样显示「服务没有提供」，
不补数值、不猜状态、不换 key 重发。

---

## 0. 一句话

`PhoneWorld` 是手机侧唯一世界门面：读（`snapshot`/`operation`）**零写入**；
写只有一条路 `act()` → 网关 `submit_local_owner_action` → Resolver（锁 + revision + 后置条件 + 账本）
→ `body_consequence`（一次性）；**到达结算与恢复推进只发生在 owner tick（`WorldBodyService.tick()`）**。

身份只来自构造函数：`local_owner_uid == os.getuid()`、`subject == economy.OWNER ("chiyo")`、
`hermes_home` 绝对路径且存在。请求体里出现 `uid/authority/home/subject/elapsed_seconds/身体值`
一律 `rejected/INVALID_PARAMS`（网关侧另有一层结构性拒绝）。

---

## 1. `PhoneWorld`

```python
from components.world.scripts import world_phone_world
phone = world_phone_world.PhoneWorld(
    hermes_home="/abs/home",          # 含 data/world、data/body、state/、run/
    environment=dict(os.environ),     # 本次进程环境；世界包由 CHIYO_WORLD_PACKAGE 决定
    local_owner_uid=os.getuid(),
    subject="chiyo",
    gateway_socket=None,             # 缺省 <hermes_home>/run/gateway.sock
)
```

绑定失败时不读不写任何文件，三个方法都返回

```json
{"kind":"world_phone_world","schema_version":"world.phone.world.v1",
 "ok":false,"status":"rejected","reason_code":"LOCAL_OWNER_UID_MISMATCH","data":{}}
```

`reason_code ∈ {LOCAL_OWNER_UID_MISMATCH, LOCAL_OWNER_SUBJECT_MISMATCH, LOCAL_OWNER_HOME_MISMATCH}`。

### 1.1 信封（三个方法共用）

```json
{"kind":"world_phone_world","schema_version":"world.phone.world.v1",
 "ok":true,"status":"applied","data":{...},"reason_code":"…（仅失败时出现）"}
```

| status | 含义 | P 的 HTTP |
|---|---|---|
| `applied` | 本次真的提交了 | 200 |
| `already_applied` | 同一 `operation_key` 的重放，后果不重复 | 200 |
| `rejected` | owner 明确拒绝，未写任何东西 | 409 |
| `disabled` | 特性开关关闭（世界/基底/旅程） | 409 |
| `unavailable` | owner 读不到或服务不在，**不是**成功 | 503 |
| `unknown` | 结果未知，必须用**同一个** key 查 `operation` | 202 |
| `not_found` | 该 key 从未使用过（仅 `operation`） | 404 |

---

## 2. `snapshot()` — 只读总览（`GET /api/phone/world`）

`data` 固定九块：`world` / `location` / `local_environment` / `destinations` / `travel` /
`objects` / `body` / `actions` / `recent_executions`。每块都带 `status`，失败时另带 `reason_code`。

**零写入**（见 §6）：不初始化、不发钱、不物化、不 `realize_due_travel`、不写 `world_state.json` /
`body_substrate.json` / `runtime.json` / 账本 / 待办，也不在 GET 上落盘迁移（老档只在内存里升级）。

### 2.1 `data.world`

```json
{"status":"available","source":"world_state",
 "world_id":"shiomi_city","revision":41,"schema_version":"world.foundation.v0",
 "timezone":"Asia/Tokyo","scene_id":"home.bedroom",
 "local_time":"2026-10-21T09:12:00+09:00","local_date":"2026-10-21","weekday":3}
```

`status="unavailable"` 时 `reason_code ∈ {WORLD_NOT_INITIALIZED, WORLD_STATE_UNREADABLE, WORLD_STATE_INVALID}`，
其余数值字段置 `null`。

### 2.2 `data.location`

```json
{"status":"available","source":"world_state+world_topology",
 "node":"home/bedroom","place_id":"home","area_id":"bedroom",
 "name":"家","kind":"home","home":true}
```

`node` 是规范化导航节点（`place` 或 `place/area`，入口区归 `place`）；`area_id` 可为 `null`。
未知地点名回落 `place_id`，不编造。`reason_code ∈ {WORLD_NOT_INITIALIZED, WORLD_LOCATION_INVALID}`。

### 2.3 `data.local_environment`

```json
{"status":"available","source":"world_package_scene",
 "building":{"place_id":"home","name":"家"},
 "area":{"area_id":"bedroom","name":"卧室","kind":"room"},
 "furniture":[{"object_id":"basic_bed","name":"床","kind":"surface","note":""}],
 "furniture_total":1,
 "adjacent_areas":[{"node":"home","name":"家","kind":"home"}],
 "note":"furniture 只含当前区域、世界包声明过的摆位；上限 8 件"}
```
`node` 是规范化导航节点（`place` 或 `place/area`）。**世界包没有室内导航时节点就是 place 本身**
（`area_id` 仍单独给出，例如 `node="home"`、`area_id="bedroom"`）；入口区一律归 `place`。
未知地点名回落 `place_id`，不编造。`reason_code ∈ {WORLD_NOT_INITIALIZED, WORLD_LOCATION_INVALID}`。
世界包**没有**声明室内导航时 → `status="unavailable"`、`reason_code="WORLD_HAS_NO_INDOOR_DECLARATION"`、
`building/area=null`、`furniture=[]`（汐见市默认世界就是这种，不拿别处的家具充数）。

### 2.4 `data.destinations`

```json
{"status":"available","source":"world_daily_opportunities","scope":"NOW_ONLY",
 "current_node":"home/bedroom","walk_seconds_max":1800,
 "places":[{"location_id":"convenience_store","name":"便利店","kind":"shop","here":false,
            "walk_seconds":150,"reach":"reachable","route":["nearby_street","convenience_store"],
            "availability":{"status":"OPEN","reason_code":null,"source":"world_package_schedule"},
            "affordances":[{"affordance_id":"BUY_SNACK","description":"…"}],
            "indoor_areas":[{"area_id":"entrance","name":"入口区","kind":"entrance"}],
            "observed":{"status":"not_observed","reason_code":"OUT_OF_VIEW"}}],
 "nodes":[{"node":"nearby_street","place_id":"nearby_street","area_id":"nearby_street",
           "label":"附近街道","kind":"place","walk_seconds":90,"reach":"reachable",
           "direct_move_available":true,"route":["nearby_street"]}]}
```

* `nodes` 是可直接当 `MOVE` 的 `destination_location_id` 用的规范化节点全集（place 级 + 所有已声明室内区域），
  按 `walk_seconds` 升序、上限 40；`walk_seconds` 来自世界拓扑 `WorldData.route_to` + `route_duration_seconds`。
* **`direct_move_available` 是硬事实**：Resolver 的 `MOVE` 只走**一条边**（`world.reachable_of`）。
  多跳目的地 `direct_move_available=false`，U 必须显示「需要分几步」，不能当作一次命令成功。
  多次 `MOVE` 各用各的 `operation_key`，服务端**不会**替用户串成一条 Activity。
* `availability.status ∈ {OPEN, CLOSED, UNKNOWN}`：`CLOSED` 不是 MOVE 的拒绝理由（走到门口合法），
  但店内可做事项为空。
* 不可达节点不列出；`scope="NOW_ONLY"` 不是动作授权（沿用 `world_daily_opportunities.action_grant=false`）。
* `status="unavailable"` 时 `reason_code ∈ {WORLD_NOT_INITIALIZED, WORLD_LOCATION_INVALID,
  WORLD_PACKAGE_UNREADABLE, WORLD_DATA_UNAVAILABLE}`，`places`/`nodes` 为 `[]`。

### 2.5 `data.travel`

```json
{"status":"in_progress","source":"world_travel_delivery.observe_travel",
 "travel_id":"travel-…","execution_id":"phone-exec:…",
 "origin_location_id":"home","destination_location_id":"nearby_street",
 "route_id":"home->nearby_street","route_revision":"shiomi-walk-v1",
 "route_duration_seconds":90,
 "started_at":"…","expected_arrival_at":"…","arrived_at":null,
 "elapsed_seconds":31,"remaining_seconds":59,"progress_band":"underway",
 "location_unchanged":true,"body_load":"pending","body_load_source":"body_consequence_ledger"}
```

* `status ∈ {none, in_progress, arrived, disabled, unavailable}`。`none` = 此刻没有在途；`disabled` = 计时旅程关闭
  （`CHIYO_WORLD_TRAVEL_TIME_ENABLED` 未开，MOVE 瞬时落地）。
* `progress_band ∈ {just_started, underway, almost_there}`（`remaining<=20` → `almost_there`，
  `elapsed/duration<0.34` → `just_started`）。只给档位，不给百分比。
* `location_unchanged=true`：**读这次快照没有推进世界位置**。出发成功后 `location` 仍是起点，
  U 不得显示「已到达」。
* `body_load ∈ {pending, settled, unknown}`：这次行程的**身体负荷**是否已结算（只读账本得出）。
  `pending` 表示出发已记录、负荷尚未落地——**不伪装成完整连续测量**。

### 2.6 `data.objects`

```json
{"status":"available","source":"world_body_substrate","substrate_revision":7,
 "here":[{"object_id":"item-…","label":"virtual_snack_wafer","placement_kind":"room",
          "placement":{"kind":"room","location_id":"home","area_id":"bedroom"},
          "portable":true,"portable_source":"world_body_binding.object_capability"}],
 "hands":{"left_hand":null,"right_hand":"item-…"},
 "held":["item-…"],
 "counts":{"here":1,"hands":1}}
```

* `here` 只含**当前地点**的摆放；别处的物件不列。
* `portable ∈ {true,false,null}`：`null` = 没有声明能力，**不可当可拿**（能力门在 Resolver，不在这里）。
* `status="unavailable"` → `reason_code ∈ {SUBSTRATE_NOT_INITIALIZED, SUBSTRATE_STATE_ERROR}`（坏档不读成空）。

### 2.7 `data.body` — 只能看到姿势、档位、恢复状态

```json
{"status":"partial","reason_code":"WORLD_BODY_SERVICE_UNAVAILABLE",
 "body_id":"chiyo_body","pose":"lying","orientation":"south",
 "pose_source":"world_body_substrate",
 "recovery":{"state":"active","mode":"sleep_like","trend":"improving","progress_band":"mild"},
 "signals":[{"signal_type":"recovery_underway","qualitative_band":"mild"}],
 "signals_status":"available","signals_source":"world_body_gateway_api.get_body_signals",
 "bands_only":true,"raw_values_exposed":false,
 "rest_evidence":"pose:lying@bed_surface",
 "note":"pose 是 World 的测量；recovery/bands 是 Body 的投影；这里没有裸浮点"}
```

* `status ∈ {available, partial, unavailable}`：
  `available` = 姿势与 Body 投影都读到；`partial` = 只读到姿势（网关 socket 不在/服务未就绪）；
  `unavailable` = 连姿势都读不到（`SUBSTRATE_NOT_INITIALIZED` / `SUBSTRATE_STATE_ERROR`）。
* **绝无** `fatigue/activation/comfort/sensory_load/tension/warmth` 或任何浮点；
  `signals` 上限 8 条，`qualitative_band ∈ {mild, noticeable, strong}`（`body_signal.BANDS`）。
* `rest_evidence`：当前姿势 + 当前地点真有世界声明的休息面（`world_body_substrate.SURFACES`，
  如 `bed_surface`）时才给 `pose:<pose>@<surface_id>`，否则 `null`。
* 没有权威环境数据源，`data.body` **不提供**感官强度/温度（`sensory_load`/`warmth` 的生产者仍不存在，
  不编噪音温度）。

### 2.8 `data.actions`

```json
{"status":"available","source":"world_body_gateway_api.get_available_actions",
 "local_owner_verbs":["MOVE","PICK_UP","PLACE","POSE","REST"],
 "verbs":[
  {"verb":"MOVE","available":true,"blocked_reason":null,"detail":null,
   "params":{"destination_location_id":"string"},
   "candidates":[{"location_id":"nearby_street","label":"附近街道","kind":"street"}]},
  {"verb":"REST","available":false,"blocked_reason":"RECOVERY_NOT_NEEDED",
   "detail":"fatigue is below the recovery threshold; rest is not needed right now",
   "params":{"state":["start","stop"],"mode":["rest","sleep_like"]},
   "start_available":false,"stop_available":false,"sleep_like_available":false,
   "start_blocked_reason":"RECOVERY_NOT_NEEDED","stop_blocked_reason":"NO_OPEN_RECOVERY",
   "current":{"pose":"standing","recovery_state":"inactive","travelling":false,"rest_surface":false}}
 ]}
```

* `blocked_reason` 是**能力事实**，不是「这次失败」：`HAND_OCCUPIED` / `NOT_REACHABLE` / `NO_TAKEABLE_OBJECT` /
  `nothing_held` / `TRAVELLING` / `REST_REQUIRES_RESTING_POSE` / `RECOVERY_NOT_NEEDED` /
  `RECOVERY_ALREADY_ACTIVE` / `NO_OPEN_RECOVERY`。
* `available=false` 的动词**提交一定被拒**（零变更），`reason_code` 用该动词的 `blocked_reason`
  精确回答（REST 再按 `state` 细分：`start` 给 `start_blocked_reason`，`stop` 给 `NO_OPEN_RECOVERY`）；
  只有「动词根本不属于本 surface」才回 `NOT_EXPOSED`。REST 的细分理由也直接写在 `actions` 里，不靠 POST 猜。
* `status="unavailable"` → `reason_code="WORLD_BODY_SERVICE_UNAVAILABLE"`，`verbs=[]`（`local_owner_verbs` 仍在）。

### 2.9 `data.recent_executions`

```json
{"status":"available","source":"world_execution_ledger","limit":5,
 "items":[{"execution_id":"phone-exec:…","action_type":"MOVE","state":"committed",
           "result":"SUCCESS","reason_code":"move_departed","occurred_at":"…",
           "world_mutation_refs":["world.travel=travel-…"],"local_owner":true}]}
```

倒序，`limit` 上限 20（默认 5）。`local_owner=true` = 账本 `cross_authority_refs` 里带
`world.local_owner_phone:…`（永远不会是 `verified_main_self_tool`）。

---

## 3. `act(action, params, operation_key)`

`POST /api/phone/world/actions {action, params, operation_key}`。

### 3.1 白名单与参数（精确 key 名；多余/未知 key 一律 `rejected/INVALID_PARAMS`）

| action | params | 值域 |
|---|---|---|
| `MOVE` | `destination_location_id`（必须） | 字符串 ≤96；会被规范化为导航节点（`place/entrance` → `place`） |
| `PICK_UP` | `object_id`（必须）、`slot_id`（必须） | `object_id ≤128`；`slot_id ∈ {left_hand, right_hand}` |
| `PLACE` | `object_id`（必须）、`target`（必须，对象） | `target` 与 `world_body_substrate.validate_placement_target` 完全一致 |
| `POSE` | `pose`（必须）、`orientation`（可选） | `pose ∈ {standing, seated, lying, transitioning}`；`orientation ∈ {north,south,east,west,up,down}` |
| `REST` | `state`（必须）、`mode`（仅 `start`） | `state ∈ {start, stop}`；`mode ∈ {rest, sleep_like}`，缺省 `rest` |

`CONSUME` **不在**本门面：它是手机购物 `PhoneShopping.use()` 的通道（已验收，未改）。
`elapsed_seconds`、`uid`、`authority`、`home`、`subject`、`fatigue` 等：拒绝。

### 3.2 `operation_key` 严格绑定 kind + 目标 + 参数

一个 key 只代表一个操作，映射到**同一个** `execution_id`（与手机购物同一命名空间，
`world_phone_shopping.execution_id_for`），因此跨 shopping/world **不串**：

| 情形 | 结果 |
|---|---|
| 同 key + 同 action + 同目标，已提交 | `already_applied`（后果不重复） |
| 同 key + **不同 action**（含 shopping 已用过的 key） | `rejected / OPERATION_KEY_CONFLICT`（`data.operation` 指明原操作），不执行 |
| 该 key 已被经济 purchase 用过 | `rejected / OPERATION_KEY_CONFLICT`（`data.operation="purchase"`） |
| 同 key + 同 action + **不同目标/参数** | `rejected / OPERATION_TARGET_CONFLICT`（附账本证据），不执行 |
| 未知 key | `operation()` → `not_found / OPERATION_NOT_FOUND`；**不自动换 key 重发** |

目标冲突靠账本里**已经存在的事实**判断（`before/after/world_mutation_refs` + 行程记录 + 摆放），
不新建第二份参数档。证据不足时 `data.replay_evidence="unverified"`，仍报 `already_applied` 但如实标注。

### 3.3 `data`（成功路径）

```json
{"execution_id":"phone-exec:…","action":"MOVE","operation_key":"…",
 "travel":{…同 §2.5…},
 "world":{"revision":41,"location":{"node":"home/bedroom","place_id":"home","area_id":"bedroom"},
          "location_unchanged":true},
 "body":{"applied_to_body":false,"consequence_reason":"deferred:movement_load_pending",
         "movement_settlement":"pending",
         "recovery":{"state":"inactive","mode":null,"trend":null,"progress_band":null}},
 "local_owner":{"source":"local_owner_phone","uid":0,
                "binding":{"subject":"chiyo","world_home":"/abs/home"},
                "caller":"local_owner_phone_surface"},
 "authoritative":true,"source":"resolver","replay_evidence":"matched"}
```

字段按 action 出现：`POSE`/`PICK_UP`/`PLACE` 有 `world`+`body`，`REST` 有 `body.recovery`
（**命令成功 ≠ 已经恢复**：`body.applied_to_body` 才是身体真的动了；
`advance_produced_no_change` 时为 `false`），`MOVE` 有 `travel`+`body.movement_settlement`。

### 3.4 MOVE 的诚实语义（重点）

计时旅程开（`CHIYO_WORLD_TRAVEL_TIME_ENABLED=1`）：

* `act("MOVE")` 提交的是**出发**：`status="applied"`、账本 `reason_code="move_departed"`、
  `travel.status="in_progress"`、`world.location` **不变**（`location_unchanged=true`）。
* 身体**不提前扣未来路程**：这次 consequence 记 `state="pending"`、
  `body.movement_settlement="pending"`、`applied_to_body=false`。**不会**把 150 秒的步行在出发瞬间扣掉。
* 到达只由 owner tick 结算：`WorldBodyService.tick()` 调 `world_travel_delivery.realize_due_travel`
  （唯一到达写者），随后为这次 execution **补一次**负荷：
  `movement_duration_seconds = min(实际经过秒数, 该行程的 route_duration_seconds)`。
  同一 execution 只结算一次（consequence id 固定）。
  `movement_measurement.source ∈ {measured_travel, measured_travel_recovered}`：
  前者＝本次 tick 自己结算了到达，后者＝到达已落盘、负荷在随后的 tick 才补上（同一段真实测量的两种时序）。
* 崩溃窗口：出发记录 pending，到达写盘后进程死掉 → 下次 tick 用 pending 记录 + 行程 `ARRIVED` 补结算，
  同样标 `measured_travel_recovered`，不重复、不丢。

计时旅程关：

* MOVE 瞬时落地，`reason_code="move_committed"`，位置等于目的地；
  负荷用世界拓扑的真实 `route_duration_seconds`（`movement_measurement.source="route_estimate"`）。
* 两者都拿不到时长时才回落既有常量 600s（`source="assumed_default"`，
  `notes=["movement_load_assumed:600.0s:route_duration_unavailable"]`）。

---

## 4. `operation(operation_key)` — 只读对账（`GET /api/phone/world/operations/<key>`）

```json
{"operation":"world","execution_id":"phone-exec:…","action_type":"MOVE",
 "ledger_state":"committed","ledger_result":"SUCCESS","reason_code":"move_departed",
 "world_mutation_refs":["world.travel=travel-…"],
 "travel":{…若该 execution 对应一条行程记录…},
 "reconciliation_required":false}
```

* `operation ∈ {world, purchase, use}`：本门面 / 手机购物侧（原样转发其判定，不重算）。
* 未知 key → `not_found / OPERATION_NOT_FOUND`。
* `prepared`/`uncertain` → `unknown`，P 只能查原 key；查询不执行、不写盘、不换 key。

---

## 5. REST（恢复）语义

`REST` 是**命令**，不是定时器。`body_runtime` 仍是六指标与 recovery 的唯一 owner。

**World 侧条件**（Resolver 自己判，`substrate` 是姿势 owner）：

1. 在途（`travel.status="in_progress"`）→ `rejected / REST_WHILE_TRAVELLING`；
2. `pose ∈ {seated, lying}`，否则 `rejected / REST_REQUIRES_RESTING_POSE`（`detail` 带当前 pose）；
   **要休息先换姿势**（姿势是 World 的测量）；
3. `mode="sleep_like"` 额外要求 `pose="lying"` **且**当前地点真的摆着一块世界声明的休息面
   （`world_body_substrate.SURFACES`，如 `bed_surface`；它自己的 owner 物件必须在场）→
   否则 `SLEEP_LIKE_REQUIRES_LYING_POSE` / `SLEEP_LIKE_REQUIRES_REST_SURFACE`。没有床就睡不了。

**Body 侧门槛**（服务 pre-flight，命令**不进** Resolver、不写账本）：

4. `fatigue < body_runtime.RECOVERY_OPEN_FATIGUE (0.30)` → `rejected / RECOVERY_NOT_NEEDED`
   （「此刻不需要恢复」；**不为演示调阈值**，也不返回任何浮点）；
5. 已 `active` 再 start → `rejected / RECOVERY_ALREADY_ACTIVE`（互斥；不会产生第二个会话）；
   stop 而当前不是 `active` → `rejected / NO_OPEN_RECOVERY`。

**成功后的事实**：账本 `world_mutation_refs=["recovery:start:<mode>"]`（或 `["recovery:stop"]`），
`body_consequence` 给 `body_runtime.advance` 一条 `{"class":"RECOVERY","state":"start|stop","mode":…}`；
六指标速率、阈值、`POSE_STRAIN` **一个字不改**。

* 推进：只有 `active` 且 tick 间隔 ≥ `MIN_ADVANCE_SECONDS`(60s) 才 `catch_up`（`rest`×3.0、
  `sleep_like`×4.8 的 fatigue 衰减）；`fatigue ≤ 0.15` → `completed`（既有规则）。
* 打断：`active` 期间任何真实物理负荷（MOVE 到达结算 / POSE / PICK_UP / PLACE / CONSUME）→
  `interrupted` + `trend=worsening` + `recovery_interrupted`（noticeable）。既有数学，未改。
* 幂等/重放：同 key 再发 → `already_applied`，不二次开门；换 key 再 start 且已 active →
  `RECOVERY_ALREADY_ACTIVE`。
* 跨重启：`runtime.json` 里的 `recovery` 就是真相；`startup()` 的 `catch_up` 续接 `active` 并标
  `partial`（单步封顶 14 天）。**停机期间不补造任何动作或思考**。

---

## 6. 只读保证与「谁写」

* `snapshot()` / `operation()` 不写：`world_state.json`、`body_substrate.json`、`runtime.json`、
  `transitions.jsonl`、`consequences.jsonl`、`world_property_economy.json`、`world_travel_delivery.json`、
  账本、授权 registry。不调 `realize_due_travel` / `observe_world` / `read_move_state` / `plan_move_to`
  （四者都会顺手结算到达）。旅行用只读的 `observe_travel`，世界状态用 `wls.read_world_state`，
  室内来自世界包场景声明，机会来自 `location_opportunity_catalog`。
* 老 schema 只在内存里升级（`SubstrateStore.load_with_migration`），GET 上没有迁移写。
* 唯一写路径：`PhoneWorld.act()` → 网关 → Resolver（锁 + revision + 后置条件）→ 账本 →
  `body_consequence`（一次性）→ `body_runtime`。到达与恢复推进只在 owner tick。

---

## 7. `reason_code` 全量（新增项在此表；旧项沿用 Resolver/合同既有拼写）

门面/绑定：`LOCAL_OWNER_UID_MISMATCH` / `LOCAL_OWNER_SUBJECT_MISMATCH` / `LOCAL_OWNER_HOME_MISMATCH` /
`INVALID_OPERATION_KEY` / `UNKNOWN_WORLD_ACTION` / `INVALID_PARAMS` / `OPERATION_KEY_CONFLICT` /
`OPERATION_TARGET_CONFLICT` / `OPERATION_NOT_FOUND` / `WORLD_BODY_SERVICE_UNAVAILABLE` /
`AUTHORIZATION_REGISTRY_UNAVAILABLE` / `SERVICE_TIMEOUT`(→`unknown`) / `LEDGER_CORRUPT` / `ECONOMY_STATE_ERROR`

网关（`submit_local_owner_action`）：`LOCAL_OWNER_PHONE_NOT_BOUND` / `LOCAL_OWNER_VERB_NOT_ALLOWED` /
`NOT_EXPOSED`（只用于「动词不属于本 surface」）/ `AUTHORIZATION_REQUIRED` / `AUTHORIZATION_*` /
`INVALID_PARAMS` / 以及被 `actions` 里该动词的 `blocked_reason` 精确回答的
`HAND_OCCUPIED` / `NOT_REACHABLE` / `NO_TAKEABLE_OBJECT` / `nothing_held` / `TRAVELLING` /
`REST_REQUIRES_RESTING_POSE` / `RECOVERY_NOT_NEEDED` / `RECOVERY_ALREADY_ACTIVE` / `NO_OPEN_RECOVERY` /
`BODY_STATE_UNAVAILABLE`

服务 pre-flight（REST，命令不进 Resolver）：`RECOVERY_NOT_NEEDED` / `RECOVERY_ALREADY_ACTIVE` /
`NO_OPEN_RECOVERY` / `BODY_STATE_UNAVAILABLE`

Resolver（既有 + 新增）：`NOT_ALLOWED` / `INVALID_PROPOSAL` / `UNKNOWN_TARGET` / `NOT_REACHABLE` /
`OCCUPIED` / `STALE_STATE` / `NOT_TAKEABLE` / `FEATURE_DISABLED` / `WORLD_UNAVAILABLE` /
`POSTCONDITION_FAILED` / `ALREADY_APPLIED` / `RECONCILIATION_REQUIRED` / `REST_WHILE_TRAVELLING` /
`REST_REQUIRES_RESTING_POSE` / `SLEEP_LIKE_REQUIRES_LYING_POSE` / `SLEEP_LIKE_REQUIRES_REST_SURFACE`

成功路径的账本 `reason_code`（`operation()` 原样读到）：`move_departed` / `move_committed` /
`pick_up_committed` / `place_committed` / `pose_committed` / `recovery_start_committed` /
`recovery_stop_committed`；`MOVE` 已在原地时账本记 `ALREADY_AT_LOCATION`（phone 侧把它归为
`NOOP` 家族，两者都不代表失败）。

`PhoneWorld` 把 Resolver 的**具体**拼写放进 `reason_code`（`NOT_ALLOWED` 这类规范码只在
`legacy_reason_code` 缺失时才兜底），`data.detail` 保留细节。

---

## 8. P 侧 HTTP（参考实现，不是 C 的写集）

| 路由 | 转发 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/phone/world` | `snapshot()` | 200 | `unavailable`→503、`rejected`→409 |
| `POST /api/phone/world/actions` | `act(action, params, operation_key)` | `applied`/`already_applied`→200 | `rejected`/`disabled`→409、`unavailable`→503、`unknown`→202 |
| `GET /api/phone/world/operations/<key>` | `operation(key)` | 200 | `not_found`→404、`unknown`→202、`unavailable`→503 |

沿用既有 CSRF/Host/Origin/`Sec-Fetch-Site` 与字段白名单；body 只允许 `{action, params, operation_key}`；
`state()` 新增 `apps["world"]` 摘要，`apps["body"]` 升级为带 `pose/recovery/signals` 的投影。

---

## 9. U 侧消费约定（字段语义在这里）

* `destinations.nodes` 画「能去哪 + 几分钟」；`direct_move_available=false` 显示「要分几步」。
* `travel.status="in_progress"` 显示「在路上，还有 N 秒/分钟」（`remaining_seconds`），**不显示「已到达」**；
  `body_load="pending"` 时可显示「负荷待结算」，不要写「已经累了」。
* `body.signals` 渲染成「身体在说什么」的短句；`recovery.state=active` 才显示「正在休息」，
  `completed` → 「休息结束」，`interrupted` → 「被打断了」。`status="partial"` 时如实写「身体投影暂时读不到」。
* REST 按钮：`available=false` 时禁用并用人话显示 `blocked_reason`
  （「现在不需要休息」/「已经开着」/「先坐下或躺下」/「没有床」/「在路上」）；
  点按成功只代表命令被接受，下一次 `snapshot()` 的 `body.recovery` 才是事实。

---

## 10. 明确不做

* 不新增第二个世界/身体/钱包 store；不加 `<home>/state/world_phone_world.json` 之类平行档。
* 不加 hunger/satiety/nutrition/energy/体温℃；`CONSUME` 不回血、不恢复。
* 不做定时作息、不做「到点宣布累」、不做后台 Agenda；`WORLD_BODY_PRODUCTION_EVENTS` 保持默认 off。
* 不自动生产事件、不让 Body 直接签发候选；改六指标、改速率、改 `POSE_STRAIN`、改
  `RECOVERY_OPEN_FATIGUE` 都不在本轮。
* 不给前端开 debug 写命令；`WorldBodyService.READ_ONLY_COMMANDS/MUTATING_COMMANDS` 保持不变。
* 不新增 Activity，不把「停留」做成状态机；不碰 `world_phone_shopping.py` 的既有购物/消耗语义。
