# v0.4 世界 / 身体界面：交付结果（U 写集）

Status: implemented（界面与纯函数层完成；真实浏览器串链由父级在同一实例里做）

基线：`v0.3.0-rc1` = `41e818faaa24115fbf43db2977b15585c04144af`，工作树 `D:\nyairo-rc1-body-backend`，
分支 `work/rc1-phone-world-body`。字段契约只认 C 的
`docs/engineering/v04-world-body-contract.md`（本文件不复述字段，只写界面怎么用它）。

## 1. 写集（实际改动）

| 文件 | 改动 |
|---|---|
| `components/phone/web/app.js` | 世界 app / 身体 app / 主屏第八入口 / owner 视图解析 / 命令 payload / 地图 SVG / 操作键接线 |
| `components/phone/web/style.css` | 世界与身体的可视样式（hero、街区图、面板、手边、折叠技术详情、窄屏） |
| `tests/test_phone_world_ui.mjs` | 新增 23 条检查（纯函数 + 静态约束 + stub DOM 接线冒烟） |
| `docs/engineering/v04-world-ui-result.md` | 本文件 |

未改动：`index.html`（不加脚本、不加内联事件）、后端、core/world、插件、`tests/` 里既有文件、
`MANIFEST.json`。

## 2. 界面结构

主屏：`HOME_ORDER` = 原七个 + `world`（第八个磁贴，图标 `map`，渐变 `.tile-world`）。
`APP_ORDER` 仍是原七个字面量，旧检查与旧入口一个都没动。

世界屏（`screen = world`，`GET /api/phone/world` 一次读取）：

1. `world-hero`：地点名（大字号）、本地日期/星期/时间（`local_time` 原串取 `HH:MM`，不换算时区）、
   状态 chip（在路上 / 正在休息 / 在家 / kind）、一句生活感描述。
   生活感描述只由 owner 事实拼（位置 + 室内区域 + 姿势 + 恢复 + 第一条信号）。
2. 在途卡（仅在 `travel.status = in_progress`）：`起点 → 目的地`、档位、剩余时间、
   「负荷待结算」、`还没到，位置仍是起点`、「到达由世界自己的 tick 结算」。
3. 街区图：自己画的 SVG（`viewBox 0 0 320 N`，`role="img"` + `aria-label` 摘要）。
   当前位置一个莓红圆点 + 「你在这里」，可达节点沿一条主干线铺成路线板；
   `direct_move_available=false` 的节点画成虚线空心圆并标「分几步」；右侧是 owner 给的步行时长。
   owner 文本只经 `textContent` 写入，属性只用固定字符串拼接。
4. 能走到的地方：`destinations.nodes` 每行 → 名称、时长、kind、`reach`、出发按钮。
   按钮禁用原因按契约顺序：在路上 → 已经在这里 → 不可达 → 要分几步 → 服务没说明 → owner 说不能。
5. 附近：`destinations.places` 每行 → 营业状态（`OPEN/CLOSED/UNKNOWN`）、步行时长、
   室内区域、能做的事（affordances）、就在这里；出发按钮只在能定位到可直达节点时出现。
6. 室内：`local_environment` 可用时才画建筑/房间/家具 chip/相邻区域（相邻区域能匹配到节点就是出发按钮）；
   `WORLD_HAS_NO_INDOOR_DECLARATION` 时如实写「这个世界包没有声明室内地图」，不拿别处家具充数。
7. 手边：左右手一格（空着 / 遮罩后的物件标签 + 「放下」），`objects.here` 每件 → `portable`
   三态 chip（能拿 / 拿不起来 / 能不能拿未声明）+ 「拿起到左手/右手」。
   **手里的 id 与屏幕上的名字分开**：`objects.handSlots` / `objects.heldObjects` 带协议原值
   （`id` / `idOk`），`objects.hands` / `objects.held` 只是展示标签；「放下」发的是原值，
   标识不可用时按钮禁用并说原因，不拿展示串冒充 ID。
8. 姿势：四个合法姿势按钮（当前姿势自动禁用）+ 朝向 `<select>`；owner 说不能换就整组禁用并给原因。
9. 休息与恢复：恢复状态、方式、程度、趋势、`rest_evidence`；三个按钮
   （开始休息 / 躺下睡一会 / 结束休息）各自按 `start_available` / `sleep_like_available` / `stop_available` 开关，
   禁用时 tooltip 与行内文字都用 `blocked_reason` 的中文（低负荷 = 「现在不需要恢复」，明说不是故障）。
10. 身体在说什么：信号短句（档位后缀）、「打开身体」按钮、说明「这里没有可调的身体数值」。
11. 刚才发生的事：`recent_executions` 前 5 条（动作、账本状态、时间、原因码人话、本地 owner chip）。
12. `<details class="tech-details">` 技术详情：九块 `block:status` chip + world_id / revision / scene_id /
    node / place_id / area_id / travel 状态 / execution_id / progress_band / body_load /
    substrate_revision / actions_status / reason 码。默认折叠。

身体屏（`screen = body`）：优先读 `/world` 的 `body` 块（带 signals 与来源），
读不到才回落 `/state` 的 `apps.body`（`body_status` 会被当成 status）；
显示大字号姿势 + 朝向 + 恢复状态、信号列表、恢复明细（状态/方式/程度/趋势/休息条件/来源）、
休息三按钮、最近动作、刷新与回到世界。没有任何浮点、没有调试开关。

## 3. 诚实规则（逐条钉在测试里）

- **出发不是到达**：`travel.status = in_progress` 时只显示「在路上 / 还有 N 秒」，
  结果卡写「已出发去 X」+「这是出发，不是到达」，并明确讲 `location` 还是起点；
  只有 `status = arrived` 才说「世界报告已经到达」。
- **姿势成功 ≠ 恢复成功**：`POSE` 结果卡不出现「恢复」字样；`REST` 只有
  `body.recovery.state = active` 才说「恢复已经开着」，`inactive` 时照实写
  「服务接受了命令，但这一次身体没有变化（advance_produced_no_change）」。
- **负荷 pending 不写成累**：`body_load = pending` → 「负荷待结算」，不写「已经累了」。
- **没有信号就是没有**：`signals` 缺失 → 「服务这次没有提供身体信号」；空数组 →
  「此刻身体没有报告信号」；`status = unavailable` → 「身体投影暂时读不到，界面不猜状态」。
- **可用性只来自 owner**：`actions.verbs[].available` / `start_available` 等没报告就不是可用；
  按钮的 `disabled` 与 tooltip 都由这些字段决定，界面不根据显示名或位置猜。
- **未知保留原 key**：scope = `world:<ACTION>:<排序后的确切 params JSON>`（含 PLACE 的嵌套 target），
  结果未知进「未确认的操作」，查询只打 `GET /api/phone/world/operations/<同一个 key>`，不换 key 重发。
- **不传身体值**：payload 只有契约里的精确 key；`uid/authority/home/subject/elapsed_seconds/fatigue`
  等即便被塞进 params 也会被 `buildWorldActionPayload` 丢掉（测试断言 payload 里没有它们）。
- **协议 id 原字节**：`destination_location_id`(≤96)、`object_id`(≤128，含手持/放下那条)、
  房间 `location_id/area_id`、`operation_key`(≤128) 都走 `protocolId()` 校验后原样回传；
  展示另用 `safeLabel()`（会长成 `item-[已隐藏的长标识]` 的东西**只**上屏，永不进请求体）。
  超长/含控制字符 → 不发请求，结果卡写「没有发出请求」；手持标识不可用时「放下」直接禁用。

## 4. 命令链路

```
按钮 → confirm*（openSheet 确认弹层：目标、时长、不会瞬移/不会自动恢复）
     → submitWorldAction(action, params)
        scope = worldActionScope(action, params)
        key   = ops.ensure(scope, {path: '/world/actions'})      ← 同一意图复用同一个 UUID
        body  = buildWorldActionPayload({action, params, operation_key})  ← 精确 key、无额外字段
        POST /api/phone/world/actions（X-CSRF-Token 来自 /state，缺失则整组只读）
     → deriveMutationOutcome → finishMutation（终态释放键 / unknown 留键）
     → refreshAfter('world') 重读 /api/phone/world
```

跨 app 的新鲜性（2026-10-21 返修）：`purchase` 与 `use`（CONSUME）也会改变这世界里的物件，
所以它们同样让同一份 `/world` 快照失效（`invalidateWorldSnapshot()`）；人正好停在
世界/身体屏上时只补读一次，不在屏上就等下次进屏重读。买到的东西回到世界屏就能看到
（手边、`portable` chip、「拿起到左手/右手」按钮都按 owner 的新事实出现），不再复用购买前的旧缓存。

P 的实测校验（`components/phone/server.py` 的 `WORLD_ACTION_FIELDS`）与本界面 payload 一致：
`MOVE{destination_location_id}`、`PICK_UP{object_id,slot_id}`、`PLACE{object_id,target}`、
`POSE{pose[,orientation]}`、`REST{state[,mode]}`（stop 不带 mode，start 缺省 `rest`）。

## 5. 验证（真实命令、真实退出码，新树 cwd）

| 命令 | 结果 |
|---|---|
| `node --check components/phone/web/app.js` | exit 0 |
| `node --test tests/test_phone_ui.mjs` | `# tests 34 / # pass 34 / # fail 0`，exit 0（旧七入口与协议 id 规则未回归） |
| `node --test tests/test_phone_world_ui.mjs` | `# tests 25 / # pass 25 / # fail 0`，exit 0（含 2026-10-21 的购买→回世界新鲜性与手持 69 字原字节两条回归） |
| `git diff --check` | exit 0 |

`tests/test_phone_world_ui.mjs` 覆盖：`node --check` 子进程、九块解析与 missing、body 白名单
（塞 `fatigue/activation/comfort/sensory_load/tension/warmth` 与 `fatigue_rate` 都进不来，dump 里没有
`0.42`/`0.0021`）、信号缺失/空/未知、五种动作的 payload 精确 key 与值域、长 object_id 原字节、
`operation_key` 的 scope 绑定、未知保留原键、owner 可用性（含 `RECOVERY_NOT_NEEDED` 与未报告）、
拿起/放下（`portable` 三态、手容量、房间缺失）、在途不提前到达、姿势≠恢复、地图计划、
营业/距离/可达性、`worldOperationLines` 未知与对账，另加**stub DOM 接线冒烟**：
主屏 8 个磁贴 → 点世界 → 世界屏含家/街区/附近街道/卧室/手边/技术详情 + SVG 有圆点 →
点「出发」→ 弹层「确认出发」→ 点主按钮 → 只发一条 `POST /world/actions`（带 CSRF、
`{action,params,operation_key}`、params 只有 `destination_location_id`、key 是同一个）→
结果卡含「这是出发，不是到达」且不含「已到达」→ 提交后重读 `/world`；身体屏那条同理，
断言出现站着/恢复/负荷在升高，且屏上没有任何 `0.x` 数字；**购买→回世界**那条：先读空物件快照 →
商店买一件 → 把 `/world` 的 stub 换成已含新物件的快照 → 回世界断言 `GET /world` 正好 2 次、
旧文案消失、新物件标签与「拿起到」按钮出现（去掉 `invalidateWorldSnapshot()` 的负控为 23 pass / 1 fail）；
**手持→放下**那条：用同一个 live 实例的真实 69 字 `left_hand` id 造快照，点「放下」→ 弹层确认 →
断言只发一条 `PLACE`，`params.object_id` 是原字节、`target` 仍是 `home/bedroom`，请求体里没有
「已隐藏」字样（把 `confirmPlace` 的 `hand.id` 换成 `hand.idLabel` 的负控为 24 pass / 1 fail，
`actual: 'item-[已隐藏的长标识]'`，与 Core 现场一致）。

**这不是浏览器验收**：stub DOM 只证明接线与字段搬运，不证明布局、滚动、焦点环与真实点击。

## 6. 未做 / 缺口（如实）

1. **没有真实浏览器跑过**：本 writer 不启动 live phone、不开浏览器；布局与键盘验收留给父级同实例串链。
2. **地图不是地理图**：契约只给节点与步行秒数，没有坐标/边权，所以画的是「从这里能走到的路线板」，
   多跳节点只能标「分几步」，不能替用户串成一次命令。
3. **只支持房间摆放**：`PLACE` 只提交 `{kind:'room', location_id, area_id}`。
   契约未给 `surface_id`（`local_environment.furniture` 只有 `object_id/kind`），所以界面不提供
   「放到床上」这种 surface 目标——宁可不做，也不猜 surface id。
4. **C/P 并行**：界面按 C 冻结的契约写，P 的 `/world*` 路由与 `/state` 的 world/body app
   已在本树存在，但本 writer 没有联调真实实例；`apps.world` 若缺失，界面仍会去读 `/world`（只认 `disabled`）。
5. 没有做手机端手势/长按快捷入口，也没有给世界屏加自定义图标以外的插画。

## 7. 父级真实浏览器流程（同一实例）

1. 打开手机页 → 主屏应有 **8** 个磁贴；第八个是「世界」（薄荷绿渐变），副标题「街区、房间与路程」。
2. 点「世界」：顶部应出现当前地点名（例：`家`）与大字的本地时间；下面依次是街区图（你在这里 + 圆点）、
   「能走到的地方」、附近、室内（卧室 + 家具 chip）、手边（左右手）、姿势、休息与恢复、
   身体在说什么、刚才发生的事、折叠的「技术详情」。
3. 在「能走到的地方」点某个可达节点的「出发」→ 弹层显示起点、服务给的步行时长、确认按钮 →
   确认后：结果卡出现「已出发去…」「这是出发，不是到达」，顶部出现「在路上」卡与剩余时间；
   hero 的地点名**仍是起点**（位置未变）。
4. 等 owner tick（或父级用真实等待）后再刷新：位置变成目的地、在途卡消失、最近动作里出现这次 MOVE。
5. 室内点家具旁的「拿起到左手」→ 手边显示左手有物件 → 「放下」→ 左手回到「空着」。
6. 姿势点「坐下」→ 姿势变「坐着」；回到休息卡：「开始休息」应可用（若 owner 说需要恢复），
   点确认后结果卡说明命令被接受，恢复状态要看下一次读取；低负荷时应看到「现在不需要恢复」。
7. 切到「身体」：应有大字号姿势、信号短句、「只有档位 / 没有裸数值」chip、恢复明细与三个休息按钮。
8. 窄屏（≤900px 侧栏隐藏、≤430px 磁贴 50px）：街区图与所有面板不得出现横向滚动条；
   Tab 键应能依次聚焦磁贴、出发按钮、姿势按钮、`<select>`、`<details>` 的 summary。
9. 断网/停服务后重进世界：应显示「未连接/世界读不出来 + 重试」，不出现任何示例街区。
