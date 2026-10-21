# Agent Note: v0.4 世界/身体界面——owner 事实驱动的街区、房间与在路上

Status: implemented

## Problem

上一轮手机只有七个入口，世界与身体对用户来说等于不存在：`/api/phone/state` 的 body 磁贴只有一行只读字段，
没有任何地方能看到「人在哪儿、周围有什么、走过去要多久、手里拿着什么、身体现在什么状态」。
本轮要在**不新增第二套账本**的前提下把这些做成可感知的界面，而这恰好是最容易造假的界面：

- 移动一旦被画成「点了就到」，就会掩盖 owner「出发是出发、到达由 tick 结算」的真实语义；
- 休息按钮一旦点完就显示「正在恢复」，就会把「命令被接受」冒充成「身体真的在推进」；
- 街区/室内如果按世界包的 display 名字渲染，就会把「看起来像床」当成「床」，把不可达画成可达；
- body 六指标一旦漏到前端，界面就会开始自己算百分比、自己宣布「累了 / 恢复了」；
- 操作键如果按 UI 状态而不是「action + 确切参数」生成，两次不同的出门会共用一个键，重放语义直接错位。

## Decision

`components/phone/web/{app.js,style.css}` 追加第八个 app（世界）并把身体 app 升级为真实投影，
字段只认 C 冻结的 `docs/engineering/v04-world-body-contract.md`：

1. **入口不破坏旧契约**：`APP_ORDER` 保持原来七个字面量（旧检查逐字对齐），新增
   `WORLD_APP='world'` 与 `HOME_ORDER = APP_ORDER.concat(['world'])`；主屏网格用 `HOME_ORDER`，
   `stateView()` 也按 `HOME_ORDER` 取 app，于是 `/state` 里的 `apps.world` 能被认出来，
   没报告时如实标 `missing/APP_NOT_REPORTED`。世界屏的准入只拦 `status='disabled'` 与连接态，
   `apps.world` 缺失也照样去读 `GET /world`，让 owner 自己回答。
2. **视图层是白名单搬运**：`worldView()` 把九块拆成 `header/location/indoor/destinations/travel/
   objects/body/actions/recent`，每块自带 `status`，缺块标 `missing`；`bodyView()` 只拷
   `pose/orientation/pose_source/recovery{state,mode,trend,progress_band}/signals/signals_status/
   bands_only/raw_values_exposed/rest_evidence`，任何没列出的字段（尤其 `fatigue` 一类浮点）都进不来。
   协议值（`node`/`place_id`/`area_id`/`location_id`/`object_id`）走 `rawProtocol()` 保留原字节，
   屏幕另用 `safeLabel()` 的 label——两者不共用同一个值。
3. **可用性只来自 owner**：出门按钮的禁用原因按固定顺序取自 owner 事实
   （在途 → 已在此地 → `reach` → `direct_move_available` → `verbs.MOVE.available` / `blocked_reason`）；
   休息三个按钮分别绑 `start_available / sleep_like_available / stop_available`，没报告就是禁用，
   并用 `blocked_reason` 的中文说话（`RECOVERY_NOT_NEEDED` = 「现在不需要恢复」，明写不是故障）。
4. **payload 由契约重建，不从视图对象透传**：`buildWorldActionPayload()` 逐动词白名单重建
   `{action, params, operation_key}`，`MOVE{destination_location_id}`、`PICK_UP{object_id,slot_id}`、
   `PLACE{object_id,target:{kind:'room',location_id,area_id}}`、`POSE{pose[,orientation]}`、
   `REST{state[,mode]}`；多余 key（`uid/authority/home/subject/elapsed_seconds/…`）一个字都不进请求体。
   与 P 的 `WORLD_ACTION_FIELDS`（allowed/required 精确集合）逐项一致。
5. **操作键绑 action + 确切参数**：`worldActionScope(action, params)` = `world:<ACTION>:<排序后的 params JSON>`，
   嵌套 `target` 也进 scope；同一个键只代表一个意图，`ops.ensure()` 复用、终态释放、`unknown` 保留并进
   「未确认的操作」，查询固定打 `GET /api/phone/world/operations/<同一个 key>`，绝不换键重发。
6. **画面自己画，不注入 owner 文本**：街区图是 `createElementNS` 画的路线板（当前位置莓红圆点 + 主干线 +
   可达圆点 + 虚线「分几步」+ 右侧步行时长），文本只经 `textContent`，属性只用固定串拼接，
   `role="img"` + 中文 `aria-label` 摘要；真正的交互仍然是下面那些真实 `<button>`。
7. **技术细节折叠**：`<details class="tech-details">` 里放九块 `block:status` chip 与
   id/版本/原因码，主视图只留生活感（地点、时间、一句「在卧室里坐着 / 走在去便利店的路上，还有 59 秒」）。
8. **诚实文案写死在渲染里**：`MOVE` 的 `travel.status='in_progress'` 只说「已出发去 X」「这是出发，不是到达」，
   `body_load='pending'` 只说「负荷待结算」；`POSE` 结果不提恢复；`REST` 只有 `recovery.state='active'`
   才说「恢复已经开着」，`inactive` 时照实写「服务接受了命令，但这一次身体没有变化」。

## Alternatives considered

- **直接用旧树 `D:\nyairo-v04-backend` 里的世界 UI 半成品**（`WORLD_ACTION_TEXT`/`bodyView`/817–1168 行那块） —
  立论：看起来省一半工作量。否决：那段是上一轮的未完成片段，`pureApi` 没导出、`renderBody` 没引用，
  全是死代码；而且它按「display 名可用」的门槛写，和冻结契约的 `verbs[].available` 冲突。本轮在新基线
  （rc1 + 已验收手机）上从契约重写，旧片段只当反例读。
- **在主屏直接塞一条「世界状态条」代替独立 app** — 立论：少一屏，主屏更像生活。否决：街区图、室内、
  手边、姿势、恢复加起来远超一屏；折叠又会把「可感知」压回「一串数字」。第八个入口 + 分层卡片更清楚。
- **地图按世界包节点坐标画地理图** — 立论：有真实地图才有沉浸感。否决：契约 `destinations.nodes` 只给
  `route` 与 `walk_seconds`，没有坐标、没有边权；自己造坐标就是假地图。现在画的是「从这里能走到的路线板」，
  并在图上写明「虚线圆点要分几步」。
- **PLACE 同时支持 surface（放到床上）** — 立论：床就在房间里，放上去更自然。否决：
  `local_environment.furniture` 只给 `object_id/kind`，没有 `surface_id`（`bed_surface` 那种 id 在世界声明的
  `SURFACES` 里，快照没暴露映射）。猜一个 surface id 就等于让用户在界面上提交一个不存在的目标；
  现在只提交「放进当前房间」，在地方级节点（`area_id=null`）上直接禁用并说明原因。
- **用前端算术画在途进度条（elapsed/duration 百分比）** — 立论：进度条比文字直观。否决：契约明确
  「只给档位，不给百分比」，前端拿两个数相除就是自己造了一个 owner 没给的事实，还会在 tick 抖动时跳变。
  现在只显示档位人话 + `remaining_seconds` 的时长文本。
- **REST 按钮点完就本地切成「正在休息」** — 立论：反馈快。否决：命令成功 ≠ 恢复生效
  （`advance_produced_no_change` 时身体没动），本地假状态会盖住 owner 的真实恢复状态。
- **把 body 的浮点四舍五入后显示** — 立论：用户想看「精力 32%」。否决：本轮不做数值呈现，只做档位/信号；
  一旦显示数字，界面就有了第二个身体真相来源。

## Consequences

- **收益**：世界从「后端能力」变成一屏可操作的东西：当前位置、街区路线板、可达目的地与 owner 给的分钟数、
  在途卡（剩余时间 + 负荷待结算）、室内家具、手边物件拿起/放下、四种姿势、三个休息按钮、
  身体信号短句与恢复明细，全部可追溯到快照字段；技术细节默认折叠。
- **诚实性代价**：屏幕上会出现不少「服务没有说明 / 暂时读不到 / 现在不需要恢复」。这是刻意的——
  这些位置原本最容易被一句假状态填满。
- **不写数据**：界面不初始化世界、不发钱、不物化、不推进 tick；`GET /world` 只读，
  只有用户点确认的五种命令才会产生写入，且都走 P 的 HTTP 与 C 的 Resolver。
- **测试形态**：`tests/test_phone_world_ui.mjs` 23 条，其中包含一个 stub DOM 接线冒烟（主屏 → 世界 →
  出发 → POST → 结果卡 → 重读；身体屏同理）。它证明接线与字段搬运，**不**证明布局/滚动/焦点环；
  真实浏览器串链留给父级在同一实例里做。旧 `tests/test_phone_ui.mjs` 34 条全绿，说明七个入口、
  协议 id 与 pending 键规则没有被这次扩张带偏。
- **已知缺口**：地图不是地理图；PLACE 只有房间目标；`surface/container/in_transit` 三种摆放目标在界面上
  没有入口（契约没给映射）；没有联调真实实例（C/P 并行，契约先行）。
- **后续**：若 C 之后在快照里给出 `surface_id` 映射或 `places` 的坐标，可以只改 `worldMapPanel`
  与 `worldPlaceOption` 两个函数，不需要动 payload 与键的作用域规则。
