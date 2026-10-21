# v0.4 世界/身体：P 侧 HTTP 与工具接线结果（rc1 + 手机基线上继续）

Status: implemented（P 写集；字段与语义只按 C 的 `docs/engineering/v04-world-body-contract.md`）
基线：`v0.3.0-rc1` = `41e818faaa24115fbf43db2977b15585c04144af`，工作树
`D:\nyairo-rc1-body-backend`，分支 `work/rc1-phone-world-body`（未 stage/commit/push/tag）。
本文件只记录 P 写集的事实；世界/身体 owner 的字段、REST 参数与 reason_code 全部来自 C 的契约，
P 不重算、不翻译、不补值。

## 1. P 写集与实际改动

| 文件 | 改动（行号为本树实测） |
|---|---|
| `components/phone/service.py` | `WORLD_ACTIONS`（56）、`WORLD_SNAPSHOT_BLOCKS`/`BODY_VIEW_FIELDS`（60/67）、`PhoneWorld` 门面接线 `_world_instance/_world_call/world/world_action/world_operation`（553 起）、`world_summary`（652）、`_body_entry`（695）；`APPS` 追加 `world`，`/state` 里 world/body 两个 app 都由**同一次**快照喂 |
| `components/phone/server.py` | `POST /api/phone/world/actions` 的 `{action,params,operation_key}` 白名单（`WORLD_FIELDS`/`WORLD_ACTION_FIELDS`/`WORLD_FORBIDDEN_PARAM_KEYS`，70 起）、`GET /api/phone/world`（411）、`GET /api/phone/world/operations/<key>`（110）、`_post_world_action`（488）、`_world_target/_world_params`（550/573） |
| `components/phone/demo.py` | `--with-world`：worldgen 室内（`_prepare_world`，388）、能力声明（`_package_capabilities`，360 / `_write_capabilities`，257）、substrate 只在**全新且 pristine** 的根里初始化一次；owner tick 可配（`DEFAULT_WORLD_TICK_SECONDS = 5.0`，60；`_world_child` 传 `--tick-seconds`，214）；实例 `features` 里补上 `CHIYO_WORLD_TRAVEL_TIME_ENABLED=1` / `CHIYO_WORLD_RUNTIME`，让 P 进程与它起的 World 子进程读同一组开关 |
| `scripts/phone.py` | `prepare-demo --with-world`（47）、`serve --tick-seconds`（70、80） |
| `chiyo_bundle/phone_tool.py` | `chiyo_phone` 新增 `world`（读）/`world_action`（写）/`world_operation`（对账），逐动词参数白名单与身份拒绝（65/66、408/430、527/560） |
| `tests/test_phone_world_api.py`、`tests/test_phone_world_tool.py` | 本文件第 6 节列出的正负用例 |

未触碰：`components/world/**`、`components/phone/web/**`、`docs/USER_GUIDE.zh-CN.md`、
旧 `tests/test_phone_*.py`、`MANIFEST.json`、`scripts/fix_platform_world_move.py`、
`scripts/fix_weixin_world_move.py`、`chiyo_bundle/hermes_plugin.py`、`plugins/chiyo/__init__.py`。
`chiyo_phone` 已在 rc1 + 手机基线里注册过一次（`plugins/chiyo/__init__.py:59`），本轮不需要再动插件。

## 2. HTTP 面（与 C 契约 §8 一致）

| 路由 | 转发 | 成功 | 失败 |
|---|---|---|---|
| `GET /api/phone/world` | `PhoneWorld.snapshot()` | 200 | `unavailable`→503、`rejected`→409 |
| `POST /api/phone/world/actions` | `PhoneWorld.act(action, params, operation_key)` | `applied`/`already_applied`→200 | `rejected`/`disabled`→409、`unavailable`→503、`unknown`→202 |
| `GET /api/phone/world/operations/<key>` | `PhoneWorld.operation(key)` | 200 | `not_found`→404、`unknown`→202、`unavailable`→503 |

* 三个路由沿用既有闸门：Host 只认 `127.0.0.1`/`localhost`；`Origin` 同源；
  `Sec-Fetch-Site ∈ {same-origin,none}`；POST 必须带 `/state` 下发的 `X-CSRF-Token`；
  请求体 ≤64 KiB、只收 `{action, params, operation_key}`；静态文件仍是固定三个名字。
* `params` 逐动词精确白名单（C 契约 §3.1）：`MOVE{destination_location_id}`、
  `PICK_UP{object_id, slot_id∈left_hand|right_hand}`、`PLACE{object_id, target}`、
  `POSE{pose[, orientation]}`、`REST{state[, mode]}`；`mode` 只能随 `state=start`。
  `PLACE.target` 按 kind 精确键集校验（room/surface/container/hand/in_transit），
  语义仍由 `world_body_substrate.validate_placement_target` 在 owner 里判。
* 身份与身体值只在服务端：`uid`/`authority`/`home`/`subject`/`elapsed(_seconds)`/
  `fatigue`/`activation`/`comfort`/`sensory_load`/`tension`/`warmth` 等出现在 params（或
  `target`）里 → 409 `CLIENT_IDENTITY_REFUSED:<field>`；顶层多余字段按既有规则同样拒绝。
* `GET /world` 是纯读：P 不初始化、不发钱、不物化、不结算到达；`/state` 的 world/body 两个
  app 都来自同一次 `snapshot()`，body 只复制 C 的 `data.body`（`pose/orientation/recovery/
  signals/signals_status/rest_evidence/bands_only/raw_values_exposed/note`），没有裸浮点。
* 一次动作在 `phone/operations.jsonl` 里记一行 `action="world:<verb>"`、
  `operation_key`、结果与 `actor`（浏览器缺省 `user`，工具声明 `model`）。这是**来源记录**，
  不是世界/身体真相：真相仍在 Resolver 账本与 body_runtime。

## 3. `--with-world`：真实室内、真实能力、一次诚实初始化

`python scripts/phone.py prepare-demo --root <新绝对根> --with-world`

1. `one_day_life.prepare` 先建真实 Supply workspace/artifact/私人事项文档 + 真实 worldgen 世界包
   + world home（含一个空 substrate，revision 0）；
2. `worldgen.apply_interiors_to_package(package, locations=["home","cafe"])` 写世界包自己的
   `scene.json`/`objects.json`/`MANIFEST.json`（本夹具：home 10 件家具 2 墙 2 门、cafe 12 件、
   22 个 placement）；
3. 能力声明**只按世界包自己的事实**：`scene.placements[x].portable is True` **且** 动作里有
   `pick_up` → `{takeable,portable,placeable=true,fixed=false}`；其余 → fixed。绝不按名字猜；
4. substrate 只在**这一次新建的根**里、且当前档还是 pristine（`placements == {}`、
   `revision == 0`、`world_id` 相同）时，用 substrate 自己的 writer（`place_object` +
   `SubstrateStore.save(expected_revision=0)`）放两样真实东西：
   * 世界包声明的可拿物件（本夹具 `cafe_cup04`，cafe/bar）；
   * 世界包自己带 `rest` 动作的休息家具（本夹具 `home_bed01`，home/bedroom）作为
     `bed_surface` 摆在该地点；
   已有任何摆放历史的 substrate 一律 `SUBSTRATE_NOT_PRISTINE` 拒绝，不碰既有实例；
5. 真实 `initialize_genesis`（10000 JPY move-in grant，不重置、不补发）；
6. 写 `state/world_object_capabilities.json`（consumable catalog + 上一步的对象声明）。

demo 摘要里的 `world` 块列出 `interiors`、`takeable`、`rest_furniture`、`rest_surface`、
`substrate.revision/placements` 与四条 notes（真实来源、不按名字、只在新根、室内 30 秒）。

## 4. owner tick 与时间

* 到达结算与恢复推进**只**发生在 `WorldBodyService.tick()`（C 的契约 §5/§9）。P 的 `serve`
  因此给 World 子进程显式 `--tick-seconds`（默认 5.0，可在命令行覆盖）；`owner-world` 自己的
  默认仍是 3600（生产默认不动）。
* 室内移动是**真实 30 秒**路线（`world_runtime_data.AREA_MOVE_SECONDS`）；P 不缩短模拟时间、
  不给 `elapsed_seconds` 之类输入。多跳地点（如 home→street_1→cafe）需要多次 `MOVE`，
  `snapshot().destinations.nodes[].direct_move_available` 会如实标出。
* tick 只改变"何时结算"，不改变"要走多久"：本夹具 home/bedroom→home(living) 需 30 秒，
  在 tick=5s 下约 30–35 秒后到达。

## 5. 工具 `chiyo_phone`

`action` 新增 `world`（读，需 `phone.enabled`）、`world_action`（写，需 `phone.write_enabled`）、
`world_operation`（对账）。参数形状：`{"action":"world_action","world_action":"MOVE",
"params":{"destination_location_id":"home"},"operation_key":"…"}`；工具自己再做一遍逐动词白名单、
枚举与身份拒绝，然后走同一条 loopback HTTP。`world_action` 的 `status:"unknown"` 提示只查原 key
（`action:"world_operation"`），不换 key 重发；成功只代表 owner 接受，不冒充"已到达/已恢复"。
受限记忆 profile 下 `world` 仍可读（它没有私人笔记正文），`world_action` 仍受 `write_enabled` 约束。

## 6. 真实验证（WSL，`PYTHONDONTWRITEBYTECODE=1`，repo cwd）

| 命令 | 结果 |
|---|---|
| `python3 -m unittest tests.test_phone_world_api -v` | `Ran 9 tests … OK`，exit 0（真实 `--with-world` 实例 + 真实两子进程 + 真实 HTTP；含 30 秒真实到达等待） |
| `python3 -m unittest tests.test_phone_world_tool -v` | `Ran 7 tests … OK`，exit 0 |
| `python3 -m unittest tests.test_phone_api tests.test_phone_tool tests.test_phone_shopping_owner` | `Ran 45 tests … OK`，exit 0（旧手机面未被 `/state` 的 world app 与 body 升级破坏） |
| `python3 scripts/phone.py prepare-demo --root /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 --with-world` | exit 0，`ok:true` |
| 手测：`GET /api/phone/world` 连续两次 | 九个 block 全在；`world_state.json`/`body_substrate.json`/`runtime.json`/`transitions.jsonl`/`consequences.jsonl`/经济档/账本的 size+mtime 完全不变（用例 `test_world_snapshot_is_complete_and_writes_nothing`） |
| 手测：`POSE seated` | 200 `applied`；快照 `body.pose=seated`、`rest_evidence="pose:seated@bed_surface"`；同 key 重放 `already_applied`；同 key 换参数 409 `OPERATION_TARGET_CONFLICT`；同 key 换动词 409 `OPERATION_KEY_CONFLICT` |
| 手测：`MOVE home` | 200 `applied`、`travel.status=in_progress`、`route_duration_seconds=30`、`location_unchanged=true`、位置仍是 `home/bedroom`；约 35 秒后快照变为 `home`、`travel.status=none` |
| 手测：`REST start`（低疲劳） | 409 `RECOVERY_NOT_NEEDED`，快照 `recovery.state=inactive`（不假装恢复；没有为演示调阈值） |
| 手测：`PICK_UP home_bed01` / `PLACE home_bed01` | 409 `NOT_REACHABLE` / `nothing_held`（诚实拒绝） |
| 手测：`GET /api/phone/world/operations/<已用key>` / `<未知key>` | 200 `applied`（带 `execution_id`/`action_type`/`reconciliation_required:false`）/ 404 `OPERATION_NOT_FOUND` |
| 手测安全闸门 | 坏 Host 403 `HOST_NOT_ALLOWED`；坏 Origin 403；跨站 403；无/错 CSRF 403；params 里 `uid/authority/home/elapsed_seconds/fatigue` 409 `CLIENT_IDENTITY_REFUSED` |
| 交付实例 `GET /`、`/app.js`、`/style.css`、`/state`、`/world` | 200/200/200/200/200（`/app.js` 190,418 字节，已含 U 并行加入的世界 app） |

时序说明（如实）：C 在 09:51–09:52 仍在改自己的 owner 文件，那期间跑 P 用例会看到
`'GatewaySurface' object has no attribute '_economy_facts'`（C 的中间态）与一次 world 子进程
超时；C 落定后（`world_body_gateway_api.py` 09:52:04）重跑：09:56–09:58 的 16 个新用例全绿，
09:59–10:01 的 45 个旧手机用例全绿。若 C/U 在本报告之后继续改，P 侧结论以"再跑一次这两个模块"为准。

未跑（如实记录）：全库套件、24h 常驻、真实模型、真实支付、生产部署、整树 SHA。U 的
`tests/test_phone_world_ui.mjs` 属 U 写集，本轮未跑。

## 7. 交父级的已停止实例

| 项 | 值 |
|---|---|
| 根 | `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`（WSL，保留不删） |
| `instance.json` | 同根；`local_owner_uid = 0`（WSL root）；`subject = chiyo`；`world_id = coastal_town_929711` |
| 启动命令 | `cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 --port 0 --tick-seconds 5` |
| 浏览器 | stdout 第一行 JSON 的 `url`（`http://127.0.0.1:<port>/`）；每次启动新端口、新 socket generation；旧 socket 以 `.retired-<gen>` 保留 |
| 停止 | 对该进程 SIGTERM；只停它自己起的 supply/world 子进程，socket/数据/日志全留 |
| 现有事实 | 起点 `home/bedroom`（家·卧室），床 `home_bed01` 已作为 `bed_surface` 摆在此处；可拿物件 `cafe_cup04`（cafe/bar）；室内真实步时 30 秒；余额 10000 JPY；substrate revision 2 |
| 状态 | **已停止**：无 `phone.py serve`、无 `owner-world`/`owner-supply` 子进程（`ps` 实测） |

## 8. 边界与未完成（不冒充验收）

* 可拿物件与休息家具**不在一处**：床在家（`home/bedroom`），杯子在咖啡馆（`cafe/bar`）。
  worldgen 的住宅室内没有任何 `portable: true` 物件，P 不按名字把家具改成"可拿"，也不手写
  家具；去咖啡馆需 `home → street_1 → cafe` 两次真实 `MOVE`（120s + 210s）。想要"手边就有
  可拿小物件"的链，用手机商店买 `virtual_snack_wafer`（真实扣 240 JPY、真实物化到
  `home/bedroom`、catalog 里 `takeable/portable` 为真）后再 `PICK_UP`，这是真实购买而非造物。
* 本夹具 cafe 的 scene 墙/门为 0：worldgen 的咖啡馆是开放式半隔断（55% 概率才生成那半堵墙），
  这次种子没生成，属实；房间区域/家具/摆位是真的。
* `REST sleep_like` 需要 `pose=lying` 且当前地点有 `bed_surface`：在家躺下后合法；在咖啡馆则
  没有休息面，P 不替它造。
* 恢复是否真的推进取决于 body_runtime 阈值与 owner tick（≥60s 才 `catch_up`）；低疲劳时
  start 会被 owner 拒绝 `RECOVERY_NOT_NEEDED`，本轮没有为演示调整任何阈值。
* 真实模型、真实浏览器串链、同根重启的完整人工复验留给父级；本轮只跑定向测试与手测，
  U 的界面（`components/phone/web/**`）属 U 写集，P 未改。
