# Agent Note: v0.4 世界/身体 —— 手机 HTTP 与工具只做接线，世界/身体的真相仍只在 C 的 owner

Status: implemented

## Problem

世界/身体这一轮要给手机加三样用户看得见的东西：她**在哪、能去哪、走过去要多久**；房间里的
物件与**拿起/放下/姿势**；以及**请求开始/停止休息**和身体的诚实反馈。难点不在画界面，而在：

* 世界位置、摆位、姿势、到达结算分属 `world_living_skeleton` / `world_body_substrate` /
  `world_travel_delivery` / `body_runtime`，手机侧若自己算"到了""累了""在休息"，第二次刷新就会
  和 owner 打架；到达与恢复**只能**在 owner tick 里推进（GET 不能顺手结算）。
* 请求体里的 `uid`/`home`/`authority`/`elapsed_seconds`/`fatigue` 是最容易被"前端补一个值"的地
  方；一旦接受，身份的唯一边界与"身体值只由 Body 说"就同时失效。
* 演示世界必须**真有**可拿的物件、可休息的家具和真实的室内步时；但 worldgen 的住宅里没有任何
  `portable` 物件，若靠"名字像杯子就算能拿"来凑，就把 M5/M15 的能力门当摆设。
* 幂等键、重放与"结果未知只查原 key"的语义已经在手机购物里定死，世界动作必须复用同一个命名空间。

## Decision

P 只做接线，字段/参数/原因码全部按 C 的 `docs/engineering/v04-world-body-contract.md`：

* **HTTP**：`GET /api/phone/world` → `PhoneWorld.snapshot()`；`POST /api/phone/world/actions`
  只收 `{action, params, operation_key}`；`GET /api/phone/world/operations/<key>` →
  `PhoneWorld.operation()`。三者复用既有 Host/Origin/`Sec-Fetch-Site`/CSRF/字段白名单，映射沿用
  `/state` 那张表（`applied|already_applied`→200、`rejected|disabled`→409、`unavailable`→503、
  `unknown`→202、`not_found`→404）。
* **参数逐动词精确白名单**：`MOVE{destination_location_id}`、
  `PICK_UP{object_id,slot_id}`、`PLACE{object_id,target}`、`POSE{pose[,orientation]}`、
  `REST{state[,mode]}`；`mode` 只随 `start`；`target` 按 kind 精确键集校验（语义仍由 substrate
  的 `validate_placement_target` 在 owner 里判）。params/`target` 里出现身份、时长或身体值 →
  409 `CLIENT_IDENTITY_REFUSED`，不是"忽略"。
* **`/state` 加 world app、body app 升级**：world/body 两个 app 由**同一次** `snapshot()` 喂；
  body 只复制 C 的 `data.body`（pose/orientation/recovery/signals/signals_status/rest_evidence/
  bands_only/raw_values_exposed），P 不算任何值，也不把 partial 说成 available（`data.body_status`
  保留 C 自己的三档）。
* **`--with-world` 的 demo 用世界包自己的事实**：`worldgen.apply_interiors_to_package` 生成
  home/cafe 的 scene/objects/manifest；能力声明只按 `scene.placements[x].portable is True` 且
  动作含 `pick_up`；substrate 只在**新建且 pristine**（`placements=={}`、`revision==0`、world_id
  相同）的根里用 substrate 自己的 writer 初始化一次：摆可拿物件 + 把 worldgen 带 `rest` 动作的
  床声明为该地点的 `bed_surface`。已有摆放历史 → `SUBSTRATE_NOT_PRISTINE`，不碰既有实例。
* **tick 只是"何时结算"**：`serve` 给自己起的 World 子进程显式 `--tick-seconds`（默认 5s，可覆盖），
  owner-world 自己的 3600 默认不动；P 进程的实例 `features` 补上
  `CHIYO_WORLD_TRAVEL_TIME_ENABLED=1`/`CHIYO_WORLD_RUNTIME`，让 P 与它起的 owner 读同一组开关
  （否则 P 的快照会说"旅程关闭"而子进程说"开着"）。室内移动仍是世界自己的真实 30 秒，不缩短。
* **工具同实例**：`chiyo_phone` 新增 `world`（读，只要 `phone.enabled`）、`world_action`（写，要
  `write_enabled`）、`world_operation`（对账）；工具自己再校验一遍逐动词白名单/枚举/身份，走同一条
  loopback HTTP，动作在 `phone/operations.jsonl` 记成 `world:<verb>` + `actor`。
* **一次动作 = 一行来源记录**：手机只记"谁发起的、哪个 key、结果"；世界/身体真相仍在 Resolver
  账本与 body_runtime。

## Alternatives considered

1. **P 自己根据 `travel.started_at`/`route_duration_seconds` 算"是否已到达"，或顺手更新位置**。
   放弃原因：到达只有一个写者（owner tick 里的 `realize_due_travel`）；P 一算就会和真正的到达
   冲突，也让 GET 变成写操作。契约的 `location_unchanged`/`body_load` 已经把"读没有推进"写明。
2. **演示里把住宅的床头灯/椅子按名字声明成"可拿"，让卧室就有小物件**。放弃原因：那正是 M15
   禁止的"按标签猜能力"；世界包自己说 `portable:false`，覆盖它就会让之后所有"不可拿"的判定都
   失去依据。宁可如实把可拿物件放在咖啡馆（worldgen 自己的杯子），并把"家里想要可拿东西"留给
   真实购买（虚拟小点心）那条链。
3. **让 P 直接写 `body_substrate.json` 摆家具，或用 `world_instance_bootstrap` 顺便塞一个测试
   物件**。放弃原因：前者等于 P 造物；后者会把 `M13A1_TEST_OBJECT` 这类验收夹具塞进用户世界。
   最终只在新根、pristine、world_id 相同的条件下调用 substrate 自己的 writer，并且只放世界包
   自己声明过的东西。
4. **GET `/api/phone/world` 不校验 Host/Origin**（和既有 notes/wallet 的 GET 一样松）。放弃原因：
   这个快照带着她的位置、摆位和身体档位；既然本轮 new route 可以，就一并纳入同一套闸门。
5. **`REST` 直接在 P 侧检查疲劳/是否已开**（少一次转发，反馈更快）。放弃原因：阈值与 recovery
   是 body_runtime 的，P 一旦复制就会在 owner 换阈值时给出相反答案；P 只透传 `RECOVERY_NOT_NEEDED`
   /`RECOVERY_ALREADY_ACTIVE`/`NO_OPEN_RECOVERY` 等 owner 自己的码。
6. **工具让模型自由传 `params` 对象而不校验**。放弃原因：模型最容易在 params 里塞 `elapsed_seconds`
   或身体值；工具的白名单与 HTTP 的白名单一致，失败在进程内就给出类型化拒绝。
7. **tick 直接调到 1 秒"让到达更快"**。放弃原因：那会改变恢复推进的节奏（每 60 秒才 catch_up 一
   次）并放大锁竞争；5 秒足以让 30 秒室内路线在肉眼可等的时间内到达，且不改变任何时长语义。
8. **把世界字段重新包装成手机自己的 schema**（"统一一下"）。放弃原因：字段契约属于 C，P 一包装
   就会在下一轮 C 加字段时变成第二份契约；现在 P 只按白名单复制/转发。

## Consequences

* 真实链已经跑通（WSL、`PYTHONDONTWRITEBYTECODE=1`）：`prepare-demo --with-world` 生成 home 10 件
  家具/2 墙 2 门、cafe 12 件、22 个 placement；`cafe_cup04` 声明为可拿、`home_bed01` 作为
  `bed_surface` 摆在 `home/bedroom`；substrate revision 2。
* 手测：`POSE seated` → 200 `applied`，快照 `pose=seated`、`rest_evidence="pose:seated@bed_surface"`；
  同 key 重放 `already_applied`；同 key 换参数 `OPERATION_TARGET_CONFLICT`；同 key 换动词
  `OPERATION_KEY_CONFLICT`。`MOVE home` → `in_progress`、30 秒、位置不变，约 35 秒后由 tick 到达
  `home`。`REST start` 低疲劳 → `RECOVERY_NOT_NEEDED` 且 `recovery.state` 仍 `inactive`。
  `PICK_UP home_bed01` → `NOT_REACHABLE`；`PLACE` 未持有 → `nothing_held`。GET 两次前后
  `world_state.json`/`body_substrate.json`/`runtime.json`/`transitions.jsonl`/`consequences.jsonl`/
  经济档/账本的 size+mtime 完全不变。
* 验证：`tests.test_phone_world_api` 9/9、`tests.test_phone_world_tool` 7/7、旧手机面
  `tests.test_phone_api + test_phone_tool + test_phone_shopping_owner` 45/45，均 exit 0。
* 代价与边界（如实）：可拿物件在咖啡馆，家里没有（worldgen 住宅没有 portable 物件，P 不改世界
  生成器、不按名字提权）；本夹具 cafe 的墙/门为 0 是 worldgen 的开放式设计（这次种子没生成半隔断）；
  `sleep_like` 需要躺在有 `bed_surface` 的地点（家），咖啡馆只能坐下休息；低疲劳时 owner 会拒绝
  开始休息，本轮没有为演示调整任何阈值；真实模型、真实浏览器串链与同根重启的完整人工复验留给
  父级。
* 交接：`/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`（已停止，`local_owner_uid=0`），启动
  命令与现有事实见 `docs/engineering/v04-world-api-result.md` §7。
