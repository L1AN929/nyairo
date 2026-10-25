# Agent Note: 世界与身体接通——手机可以「在路上 / 拿放 / 坐下 / 真的休息」，且时间与负荷都来自真实测量

Status: implemented

## Problem

rc1（`v0.3.0-rc1` = `41e818fa…`）里世界与身体各自都有真东西，但**接不起来**，一共有四条具体的死链：

1. **恢复（REST）在产线上不可达**。`world_action_resolver.ACTIONS` 只有
   `MOVE/PICK_UP/PLACE/POSE/CONSUME`，而 `body_consequence.RECOVERY_ACTIONS=("REST",)` 要求 canonical result 的
   `action` 恰为 `"REST"`。没有任何命令能产出它，于是 `rest/sleep_like/trend/progress` 全是死状态，
   `WorldBodyService._advance_due` 的开门条件（`recovery.state=="active"`）永远不成立——**六指标在产线上几乎不随时间动**。
2. **移动的身体代价与真实路程无关**。`body_consequence.MOVEMENT_SECONDS_BY_ACTION["MOVE"]=600.0` 是写死的假设，
   注释自陈「resolver result 今天不带时长」；而世界侧 `world_travel_delivery.route_duration_seconds` 早就有真实路线秒数。
   于是 90 秒和 1800 秒的步行在身体上是同一件事。
3. **手机看不到「我在哪、能去哪、手上有什么」**。手机只有购物通道，`components/phone` 没有世界读模型；
   而世界侧「在哪/可达/营业/室内」的权威读模型本身是齐的（`world_living_skeleton.observe_world`、
   `world_daily_opportunities.location_opportunity_catalog`、`world_travel_delivery.observe_travel`），
   只是没人把它们投影成一个只读快照，且 `observe_world`/`read_move_state` 会顺手结算到达，不能直接拿来当 GET。
4. **本地 owner 只能 CONSUME**。网关 `GATEWAY_LOCAL_OWNER_VERBS=("CONSUME",)`、resolver 里
   `local_owner_authority_limited_to_consume`：操作者点一下手机，只能吃东西，不能走过去、不能坐下。

同时存在一个诚实性问题：**出发不是到达**。计时旅程下 `act("MOVE")` 提交的是出发，位置要到期才变；
如果出发瞬间就把整段路程的负荷扣掉，那就是用一个还没发生的测量冒充已经发生的负荷。

## Decision

在 rc1 之上按**写集 C**（`components/world/scripts/**`、`components/world/service/**`）把上面四条接通，
并让每一步都只走既有 owner：

1. **`REST` 成为 Resolver 的一个动词**（`ACTIONS` + `_resolve_rest`），只判**世界侧**事实：
   在途→`REST_WHILE_TRAVELLING`；`pose ∉ {seated, lying}`→`REST_REQUIRES_RESTING_POSE`；
   `sleep_like` 还要 `pose=="lying"` 且当前地点**真的摆着**一块世界声明过的休息面（`substrate.SURFACES`）→
   否则 `SLEEP_LIKE_REQUIRES_LYING_POSE`/`SLEEP_LIKE_REQUIRES_REST_SURFACE`。
   承诺一次 commit + 账本证据（`recovery:start:<mode>` / `recovery:stop`），把 `recovery_state/mode` 交给
   `body_consequence` 组成那条唯一合法的 `RECOVERY` 输入。
2. **身体侧门槛留在身体 owner 手里**：`fatigue < RECOVERY_OPEN_FATIGUE(0.30)` → 服务 pre-flight 直接
   `RECOVERY_NOT_NEEDED`（**不进 Resolver、不写账本、不返回任何浮点**）；已 active 再 start →
   `RECOVERY_ALREADY_ACTIVE`；stop 而未开 → `NO_OPEN_RECOVERY`。
   六指标、速率、`POSE_STRAIN`、阈值、`RECOVERY_MODES` **一个字没改**。
3. **实时长 + 唯一到达写者**：`WorldBodyService.tick()` 先 `realize_due_travel`（唯一到达写者），
   再为这次 execution **补一次**负荷：`movement_duration_seconds = min(实际经过秒数, 行程 route_duration_seconds)`，
   记 `movement_measurement.source="measured_travel"`（到达本就落盘、负荷在随后的 tick 补上时记
   `measured_travel_recovered`）。**出发只记 `pending`**（`body_consequence.defer_for_execution`：零输入、零身体写），
   崩溃窗口由下一次 tick 用 pending 记录 + `ARRIVED` 行程补结算，consequence id 固定所以只结算一次。
   计时旅程关闭时用世界拓扑真实 `route_duration_seconds`（`route_estimate`），两者都没有才回落 600s 并写
   `movement_load_assumed:600.0s:route_duration_unavailable`。
4. **`PhoneWorld` 是手机侧唯一门面、且 GET 零写入**：`snapshot()` 九块全部来自只读入口
   （`wls.read_world_state`、`location_opportunity_catalog`、`observe_travel`、`SubstrateStore.load_with_migration`、
   账本 `read_all`、网关 `get_body_signals`/`get_available_actions`），**不调** `realize_due_travel`/`observe_world`/
   `read_move_state`/`plan_move_to`。`act()` 是唯一写路径，`operation()` 只读对账。
   身份只来自构造参数（uid==真实 uid、subject==`economy.OWNER`、home 绝对且存在），绑定失败时连读都不发生。
5. **一个 key 只代表一个操作**：`execution_id` 由 key 派生，**与手机购物同一命名空间**
   （`world_phone_shopping.execution_id_for`），因此同 key 换动作 → `OPERATION_KEY_CONFLICT`（含「已被购物用过」）、
   同 key 换目标 → `OPERATION_TARGET_CONFLICT`（用账本里已经存在的 before/after/`world_mutation_refs` 与行程记录判定，
   **不建第二份参数档**）、同 key 同目标重放 → `already_applied` 且不二次提交。
6. **本地 owner 的可见范围**：网关 `GATEWAY_LOCAL_OWNER_VERBS` 与 Resolver `LOCAL_OWNER_ACTIONS` 是同一份
   `MOVE/PICK_UP/PLACE/POSE/CONSUME/REST`（有测试固定二者相等）；账本为**每个**本地 owner 条目写
   `world.local_owner_phone:…`，结果盖 `authority="local_owner_phone"`，永远不写 `verified_main_self_tool`。
   被拒时按 verb（REST 还按 `state`）回答**具体能力事实**，不再一律 `NOT_EXPOSED`。
7. **身体只出档位**：`data.body` 只有 `pose/orientation/recovery{state,mode,trend,progress_band}/signals[{type,band}]/
   rest_evidence`，`raw_values_exposed=false`；`progress_band` 用既有 `body_signal.BANDS`。`sensory_load`/`warmth`
   没有权威世界数据源，就**不提供**，也不编噪音温度。

## Alternatives considered

- **把 REST 做成服务里的独立命令（不进 Resolver）**：实现最短，服务本来就有 `body_state`，直接改 recovery 就行。
  否决——那会绕过锁、revision、后置条件与账本，出现「身体动了但没有执行记录」的一条侧门，而且
  `body_consequence` 的 `RECOVERY` 输入类就永远没有生产者，等于把死链 1 原地保留成第二条写路径。
- **在出发瞬间就扣整段路程的负荷（用 route_duration_seconds）**：最简单，也「看起来身体立刻有反应」。
  它甚至有个强立论：步行是**已授权、已开始**的事实，按路线时长预扣等价于一次确定性估计。
  否决——设计上身体负荷必须来自**测量**（"本体感觉位置是测量"、`13:96` 每切片要有真实状态），
  而这段路还没走完；预扣等于用一个未来量冒充已经过时长，之后到达时要么重复扣、要么无法解释为什么没变。
  所以选「出发写 pending、到达按 `min(实际经过, 路线)` 结算一次」。
- **给 substrate 加 `pose_since` 以测量姿势持续时长**（review 的建议）：能让 `posture_seconds=60` 的假设消失。
  否决——本轮不改 substrate schema 是硬约束（避免第二真相、避免对旧档做迁移写），而 60s 只影响 `comfort/tension`
  的一小段既有数学；把「姿势多久」留成已知上限，比为了它动全库格式更保守。
  （2026-10-24 更新：60s 假设已由 [持续姿态积分](2026-10-24-body-posture-elapsed-integration.md)
  按 Body 已持久游标 + World `before.pose` 取代，仍未新增 `pose_since`；本条关于"不改 substrate schema"
  的拒绝理由保持有效，不作废。）
- **让 `snapshot()` 复用 `observe_world`/`read_move_state`**：一行就能拿到最丰富的观察。
  否决——这两个入口会**顺手结算到达**（`wtd.realize_due_travel`），GET 就变成写路径；「看一眼就到达」是不可接受的语义。
- **给手机建一份世界/身体影子档（`state/world_phone_world.json`）**：读起来快，也能记住「这个 key 当时请求了什么」。
  否决——平行 store 是被明确禁止的；key→目标绑定用既有账本证据 + 行程记录判定即可（证据不足时如实标 `unverified`）。
- **把多跳 MOVE 在门面里串成一串命令**：体验上「点一次就到便利店」更顺。
  否决——那会变成门面自造的 Activity（用户已排除后台 Agenda/平行活动），而且爆炸半径是多个 execution 的原子性。
  改为快照老实给 `direct_move_available=false`，让 U 说「需要分几步」。
- **`sleep_like` 允许站着/坐着开**：更宽松，也不会出现「点了睡却没反应」。
  否决——姿势是 World 的测量，睡眠必须有躺姿与真实床面（`body_surface`/`SURFACES`）；没有床就睡不了，
  否则「睡觉」会变成一句文案。

## Consequences

- **收益**：一条真实可体验的链在同一个实例里成立——看得到在哪/能去哪/手上有什么 → 出发（在途、位置不变、负荷 pending）
  → 到达（唯一写者结算测得负荷）→ 拿放/换姿势（能力门 + 可达 + 手容量）→ 请求休息（低疲劳如实拒绝、
  合法则真的开门）→ tick 推进 → 走路到达把会话打成 `interrupted` → 重启后仍是同一个 active 会话。
  上轮已验收的手机（笔记/钱包/购物/消耗）与 rc1 的 world_move 工具/引导一字未动：购物 21 条、手机 API 13 条、
  工具 11 条、界面 34 条仍全绿。
- **代价**：世界侧多了一条 `REST` 动词与一个 `_rest_available` 能力投影；`act()` 与 `tick()` 变长（多了 pre-flight、
  到达结算、pending 补结算）；`submit_local_owner_action` 的拒绝话术从 `NOT_EXPOSED` 变成具体原因
  （`test_world_consume.py` 的 `NOT_EXPOSED` 断言走的是 `submit_action`，未受影响）。
- **已知上限**：姿势持续时长仍是既有 60s 假设（substrate 无 `pose_since`）；
        **2026-10-24 更新：该 60s 假设已由 [持续姿态积分](2026-10-24-body-posture-elapsed-integration.md)
        按已记账 elapsed 取代，未新增 substrate 字段，本条对 `pose_since` 的拒绝理由不变**；
        `sensory_load`/`warmth` 仍无世界来源；
  MOVE 只走一条边；`pending` 记录会让 consequences 账本多一条（这是为了崩溃窗口不丢负荷而故意留的）；
  `snapshot()` 每次都读整份执行账本（与既有 `reconcile` 同量级）。
- **升级触发条件**：若将来要「坐了三小时」这类真实姿势测量，应先在 substrate 加 `pose_since` 并写迁移
  （那时才动旧档），而不是在门面里用 `last_advanced_at` 猜；若 Body 要输出更多档位，应扩 `body_signal.SIGNAL_RULES`
  而不是在网关/门面里造字段。
