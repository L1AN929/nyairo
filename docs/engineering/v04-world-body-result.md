# v0.4 世界与身体：结果（C 核心 owner，rc1 基线）

Status: **implemented**（契约见 `v04-world-body-contract.md`，同轮落地并通过定向正负）
基线：`v0.3.0-rc1` = `41e818faaa24115fbf43db2977b15585c04144af`，工作树 `D:\nyairo-rc1-body-backend`
分支 `work/rc1-phone-world-body`（未 stage / 未 commit / 未 push / 未发布）
写集：`components/world/scripts/**`、`components/world/service/**` + 自有两个测试 + 自有 docs/note

---

## 1. 实际改动的文件（只这些）

| 文件 | 改动 |
|---|---|
| `components/world/scripts/world_phone_world.py`（新，1675 行） | 手机侧唯一世界门面 `PhoneWorld(hermes_home, environment, *, local_owner_uid, subject='chiyo', gateway_socket=None)`：`snapshot()` 九块只读、`act(action, params, operation_key)` 唯一写路径、`operation(key)` 只读对账。零新 store。 |
| `components/world/scripts/world_action_resolver.py` | `ACTIONS` 增加 `REST`；`LOCAL_OWNER_ACTIONS`（与网关同一份动词表）；新增 `_resolve_rest` + `_travel_in_progress` + `_rest_surface_here`；`_entry` 为**每个**本地 owner 条目追加 `world.local_owner_phone:…` provenance；`resolve()` 为所有本地 owner 结果盖章 `authority/local_owner`。 |
| `components/world/scripts/body_consequence.py` | 移动时长来源可测：`_movement_measurement()`（`measured_travel` / `measured_travel_recovered` / `route_estimate` / `assumed_default`）+ `movement_measurement` 落进 consequence；`REST` 支持 `start`/`stop` 两种 RECOVERY 输入；新增 `defer_for_execution`（出发记 `pending`、零输入、零身体写）与 `pending_movements`。 |
| `components/world/service/world_body_gateway_api.py` | `GATEWAY_LOCAL_OWNER_VERBS` 扩到 `MOVE/PICK_UP/PLACE/POSE/CONSUME/REST`；`get_available_actions` 增加 `REST`（含 `start_available/stop_available/sleep_like_available/current` 与逐方向 `blocked_reason`）；`get_body_signals` 增加 `recovery.progress_band`（三档，无浮点）；`submit_local_owner_action` 暴露拒绝改为**按 verb（REST 还按 state）给具体能力事实**，只有动词不属于本 surface 才回 `NOT_EXPOSED`。 |
| `components/world/service/world_body_service.py` | `tick()` 变成 owner 的唯一到达写者：`realize_due_travel` → 到达负荷结算 → pending MOVE 补结算 → 恢复推进；`act()` 增加 REST 身体 pre-flight（不进 Resolver、不写账本）与 MOVE 的两种诚实语义（计时出发只记 pending；瞬时移动用世界拓扑 `route_duration_seconds`）；`shutdown()` 真的停掉 serve 循环（`_stop_requested`）。 |
| `tests/test_world_body_life.py`（新） | 20 个机制用例：三份动词表一致、`_movement_measurement` 正负、consequence provenance、REST start/stop 输入、阈值仍归 runtime、负荷打断、pending→applied、`_measured_seconds` 取 min、Resolver REST（站/坐/躺、无床、在途、mode 只能配 start、重放已应用、账本 refs 与本地 owner provenance）。 |
| `tests/test_phone_world_owner.py`（新） | 16 个端到端用例（真 service + 真 gateway socket + 真 Resolver/账本/授权 registry）：绑定三码 + 零读写、GET 九块与**字节级零写入**、body 无裸浮点、MOVE 出发不扣负荷/到达只结算一次且用测得秒数、key 绑定（同 key 换动作/换目标/跨 shopping 冲突）、`operation()` 只读对账、POSE/PICK_UP/PLACE 可见性与能力门、躺姿不能拿、身体值/时长不是参数、低疲劳真实拒绝、合法休息开启/推进/停止/互斥、sleep_like 需要躺姿与真床、真实步行打断、重启后会话仍在。 |
| `docs/engineering/v04-world-body-contract.md`（新） | P/U 消费的字段契约（先写定后实施，落地后按实测修正三处：node 在无室内声明时即 place、移动测量两种时序、暴露拒绝改为具体原因）。 |

没有动的：`world_phone_shopping.py`、`components/phone/**`、`chiyo_bundle/**`、`plugins/**`、`MANIFEST.json`、
`scripts/fix_*_world_move.py`、`body_runtime.py`（六指标/速率/阈值/`POSE_STRAIN`/`RECOVERY_OPEN_FATIGUE` 一个字没改）、
`world_body_substrate.py`（schema 仍是 v1.2，不新增 `pose_since`、不加字段）。

---

## 2. 真的能体验到的链（同一实例、真实文件）

1. `snapshot()`：`world`/`location`（`home` + `area_id=bedroom`）/`local_environment`/`destinations`（含
   `walk_seconds`、`direct_move_available`、营业状态）/`travel`/`objects`（当前地点摆放 + 手持）/`body`
   （姿势 + recovery 三档 + 定性信号 + `rest_evidence`）/`actions`（五个动词 + REST 的可做/不可做原因）/
   `recent_executions`（最近 5 条，带 `local_owner=true`）。
2. `act("MOVE", {"destination_location_id": "nearby_street"}, key)` → `applied`，账本 `move_departed`，
   `travel.in_progress`，`world.location` **仍是起点**，`body.movement_settlement="pending"`，
   consequences.jsonl 里一条 `state=pending`、`inputs=[]` 的记录。
3. owner tick（真实 service 循环或显式 `tick()`）到期后：`world_state.json` 变成目的地，
   同一 execution 追加一条 `applied` 记录，`movement_measurement={"source":"measured_travel", "duration_seconds":300.0}`。
4. `act("POSE", {"pose":"seated"})` → `applied_to_body=true`；`act("PICK_UP"…“PLACE”…)` 走真实能力门；
   手里有东西时 `actions.PLACE.available=true`，躺姿下 `PICK_UP` 被拒。
5. `act("REST", {"state":"start"})`：疲劳低于 0.30 时 `RECOVERY_NOT_NEEDED`（**没有**调阈值）；
   0.60 时真的开门（`recovery.state=active`），tick 推进（注入时钟 1h：fatigue 下降、`progress_band` 变化），
   `stop` 关门（`completed`）；再 start 是 `RECOVERY_ALREADY_ACTIVE`；走路到达会把会话打成 `interrupted`+`worsening`。
6. SIGTERM/重启后 `runtime.json` 里的 `active` 会话还在，`snapshot().body.recovery.state=="active"`，
   账本仍是 `committed`，没有第二条命令、没有第二个会话。

---

## 3. 真实命令与结果（WSL，`PYTHONDONTWRITEBYTECODE=1`）

| 命令 | 结果 |
|---|---|
| `python3 tests/test_world_body_life.py` | `Ran 20 tests … OK`（exit 0） |
| `python3 tests/test_phone_world_owner.py` | `Ran 16 tests in 2.817s … OK`（exit 0） |
| `python3 tests/test_phone_shopping_owner.py`（上轮已验收手机，直接受影响） | `Ran 21 tests … OK`（exit 0） |
| `python3 tests/test_world_consume.py`（网关 CONSUME 直接受影响） | `Ran 31 tests … OK`（exit 0） |
| `components/world/service/test_world_body_service.py` | `Ran 39 tests … OK`（exit 0） |
| `components/world/service/test_world_action_authorization.py` | `Ran 14 tests … OK`（exit 0） |
| `components/world/service/test_world_object_capability.py` | `Ran 10 tests … OK`（exit 0） |
| `components/world/service/test_world_move_candidates.py` | `Ran 8 tests … OK`（exit 0） |
| `components/world/service/test_world_action_lifecycle.py` | `Ran 12 tests … OK`（exit 0） |
| `python3 -m unittest tests.test_phone_api`（P 的手机 HTTP，间接用我的网关） | `Ran 13 tests … OK`（exit 0） |
| `python3 -m unittest tests.test_phone_tool` | `Ran 11 tests … OK`（exit 0） |
| `node --test tests/test_phone_ui.mjs`（U 的界面，间接用我的字段） | `# tests 34 / # pass 34 / # fail 0`（exit 0） |

夹具：`/tmp/nyaw05r.jdK3q/rc1-world-body-*`（唯一创建、**保留**，不删、不 no-op unlink）。
时间注入只用于证明机制（service 自己的时钟前移 → tick 推进），没有声称 24h 真实运行。

---

## 4. 边界与诚实说明

* **没有新 Activity、没有平行 store**：一个 key 一个 `execution_id`（与手机购物同一命名空间），
  重放/换目标/跨域冲突全部靠既有账本与回执判定，不新增参数档。
* **没有改身体数学**：六指标、速率、`POSE_STRAIN`、`RECOVERY_OPEN_FATIGUE`、`RECOVERY_MODES` 均未动；
  REST 只是把既有 `RECOVERY` 输入类接上命令面（review 里第 1/2 条死链）。
* **没有提前扣路程**：计时出发只写 pending；到达结算用 `min(实际经过秒数, 路线秒数)`。
  两种结算时序如实分成 `measured_travel` / `measured_travel_recovered`。
* **没有编环境**：`sensory_load`/`warmth` 仍无世界数据源，`data.body` 不提供它们，也不编噪音温度。
* **姿势时长仍是既有 60s 假设**：`world_body_substrate` 没有 `pose_since`，本轮**没有**为了它改 substrate schema
  （避免第二真相与 old-data 迁移）；姿势持续时长因此不在本轮测量范围。
* **MOVE 只走一条边**：Resolver 只接受 `world.reachable_of(current)`；多跳需要多次 `MOVE`（各自 key），
  快照用 `direct_move_available=false` 明说，不偷偷串成 Activity。
* **未做**：真实浏览器串链（父级做）、24h 运行、真实模型、生产部署、`WORLD_BODY_PRODUCTION_EVENTS` 开启、
  Life/AR-0 桥（POSE 仍按既有理由拒绝）、`app.js`/手机 UI（U 的写集）。
* **已知取舍**：`submit_local_owner_action` 的暴露拒绝从「一律 `NOT_EXPOSED`」改成「按 verb（REST 还按 state）
  给具体原因」，`tests/test_world_consume.py` 的 `NOT_EXPOSED` 断言走的是 `submit_action`（未改），
  上轮手机购物 21 条仍全绿。

---

## 5. rc1 返修：PLACE 的「当前房间」与原因码（2026-10-21）

父级真实浏览器在 live 实例上「拿起成功 → 放下 409 rejected reason=UNKNOWN、手仍持有」。只读复算结论：
那次 PLACE 的 `args_digest` 只在参数为
`{"object_id": "item-[已隐藏的长标识]", "target": {"kind":"room","location_id":"home","area_id":"bedroom"}}`
时命中 —— 界面把**手持项的显示标签**（64 位 hex 被 `maskSensitive` 抹掉）当 object_id 回传，属 U 写集
（`components/phone/web/app.js`），本轮未改；合法目标本身在真 Resolver 上 `applied`。

本轮只修两个已被证实的 C 侧缺陷（详见
`.agents/notes/implemented/bug-fix/2026-10-21-v04-world-place-current-area-and-unknown-reason.md`）：

1. `_resolve_place`：room 目标必须等于世界状态声明的当前 place+area，否则在写账本之前
   `NOT_REACHABLE` + `NOT_CURRENT_AREA: …`；此前同楼另一间（如 `home/kitchen`）也会被写进 substrate。
2. 不再出现裸 `UNKNOWN`：resolver 的「无摆放」原因改成真实拼写 `UNKNOWN_TARGET:`，
   `PhoneWorld._reason_of` 把 legacy 前缀 `UNKNOWN` 当占位符、回落到 canonical code。

能力门、所有权、跨地点目的地判定、六指标/阈值、substrate schema 均未改。
`tests/test_world_body_life.py` 27 条 OK、`tests/test_phone_world_owner.py` 20 条 OK、
`tests/test_phone_world_api.py` 9 条 OK、`tests/test_phone_shopping_owner.py` 21 条 OK、
`components/world/service/test_object_action_m15.py` 20 条 OK（均 exit 0）。
**这不是浏览器验收**：父级原红 `rc1-world-browser-continuation.json` 保留，重启由父级自己做。
