# 修后 d 为何没有 MOVE：既有生成与过滤路径（2026-10-31）

## 结论

本 d 标准 bootstrap、gate off 下，**home 有直接邻居 street_1，World owner 也实际给出了 MOVE→street_1**（原始可用性里 MOVE available=true、1 个候选）。MOVE 是在 `action_intent.build_action_space` 里被 gate off 丢掉的：非 canonical 时它把 `available_actions` 整体置 None，MOVE 候选只在该 mapping 分支内产出。而 `observed_move_opportunities` 是**唯一只从该选择空间取数**的机会类型；其余机会直接读原始 `available_actions` 或地点目录。所以最终是 3 POSE＋1 STAY、0 MOVE，不是“home 没有可去的地方”，也不是“gate 把所有候选都过滤了”。

父级独立验证：固定driver实际exit0/OBSERVED，隔离根`/tmp/d-move-supply-1vn4iawd/root`，夹具时刻`2026-10-31T15:43:09+08:00`，provider记录basis=`round_last_observe`、home/standing/revision1。直接读取到raw MOVE available=true、1目标street_1；gate后MOVE=0、POSE=2；机会视图POSE=3/STAY=1。地图17地点、catalog12且truncated=true。以上是父级实际运行；下列函数/行号与过滤解释来自worker定点只读交付，父级未通读components独立复审，不混作父级源码审计。未开gate对照，静态过滤因果与实测差额分开。

父级自有runtime closed，Supply/World exit0、socket_live_after均false；graceful行均未见，不冒明确drain日志。产品模型0，数据保留，148/300不变。worker观察和父级观察是两个不同隔离根/时刻，不拼成同一快照。

## 静态路径（函数/行号/前提）

1. **World 原始可用性**：`components/world/service/world_body_gateway_api.py:1091` `get_available_actions`；其中 MOVE 条目 `available=True`，`candidates` 来自 `_reachable_move_candidates()`（同文件 `:1053`），后者读 `world_living_skeleton.location_catalog()` 里**当前地点**的 `reachable` 列表——只有直接邻居。前提是 world_state 位置合法。
2. **Gate 过滤**：`components/world/service/bridge/action_intent.py:235-239`（provider 实际 import 的这一份；`autonomous_life_assembly.resolve_world_provider:480-484` 把 bridge 目录插到 `sys.path` 前再 `import action_intent`）：`gate_state = resolve_gate()`，`if gate != canonical: available_actions = None`。MOVE 的发射只在该 mapping 分支里（`:258-275`）。非 bridge 副本 `components/world/service/action_intent.py:154-157` 是同一规则。
3. **机会翻译**：`components/life/scripts/world_move_opportunity_source.py:177-226` `observed_move_opportunities` 只遍历 `action_space["candidates"]` 并要求 `verb == "MOVE"`（`:202-205`）。
4. **读取来源不对称**：同文件 `observe()` `:1548-1633` 中，**只有 MOVE 用 `visible`（gate 后的 action_space，`:1569-1583`）**；CONSUME / PICK_UP / PLACE / POSE / REST / TOILET 直接读原始 `available_actions`，STAY 读地点目录。因此 gate off 只移除 MOVE，不移除 POSE/STAY。

一个可核对的旁证：本次 POSE 在机会视图里有 3 个（原始 owner 枚举含 `transitioning`），而 gate 后的 action_space 只有 2 个（`SUPPORTED_POSES` 减当前姿态）。这直接说明机会视图并非由 action_space 派生。

## 本次观察（新隔离，非 2026-10-31 15:27:41 那次）

- scratch `/tmp/d-move-supply-o7x2tbvo`；world `coastal_town_929711`；place `home`、pose `standing`、rev `1`；fixed `observed_at=2026-10-31T15:39:13+08:00`。gate off，产品模型 0，脚本 Decision 预留 1 次（不计产品）。
- 原始可用性（同一 provider 观察、未过 gate）：MOVE available=true / 1 候选 `[street_1]`；PLACE `nothing_held`；PICK_UP `NOT_REACHABLE`；CONSUME `NOT_HELD_OR_HERE`；REST `REST_REQUIRES_RESTING_POSE`；TOILET `LIVED_NEEDS_NOT_ENABLED`。
- gate 后 action_space：`intent_gate=off`、`object_actions=RUNTIME_DISABLED`、`candidate_kinds={POSE:2}`、`move=0`。
- 机会视图：`{POSE:3, STAY:1}`，MOVE 0。
- 层① source（同 observed_at 事后重读、各源 bounded cap 25）：World 返回 4 条（3 POSE＋1 STAY），其余 7 源为空。层② AG-0 4 条；层③ 最终请求 4 条；三层 ID 4/4 匹配（cset `cset:965188f1c7ec5bd1`）。层①是 bounded 重读，不是原请求快照。

## 地点清单（本新 World 全部 17 个，无截断）

`direct_move_targets_static` 是地图拓扑里的直接邻居，**不是机会观察、也不是动作授权**；只有观察某地点时它才可能成为 MOVE 目录。provider 只观察当前位置，所以除 home 外其它地点的机会一律 `API_NOT_QUERYABLE`（UNKNOWN）。

| location_id | kind | area | direct_move_targets_static |
|---|---|---|---|
| home | home | bedroom | street_1 |
| school | school | school | street_2 |
| beach | beach | beach | lighthouse, street_3 |
| convenience_store | store | convenience_store | street_1 |
| fish_market | market | fish_market | pier, street_3 |
| pier | public | pier | fish_market, street_3 |
| station | station | station | street_1 |
| cafe | cafe | cafe | street_1 |
| bookstore | store | bookstore | street_1 |
| library | library | library | street_2 |
| shrine | shrine | shrine | street_1 |
| park | park | park | street_1 |
| home_goods_store | store | home_goods_store | street_1 |
| lighthouse | public | lighthouse | beach, street_3 |
| street_1 | street | street_1 | bookstore, cafe, convenience_store, home, home_goods_store, park, shrine, station, street_2, street_3 |
| street_2 | street | street_2 | library, school, street_1 |
| street_3 | street | street_3 | beach, fish_market, lighthouse, pier, street_1 |

另：provider 的 `location_opportunity_catalog` 投影从 home 出发列出 12/17 个地点（`truncated=true`，`MAX_OPPORTUNITIES` 上限），含多跳 `walk_seconds`（home 30s、street_1 150s、street_2 325s…）。**多跳可达 ≠ MOVE 候选**：gateway 的 MOVE 候选只取当前地点的直接邻居（此处仅 street_1）。

## 未证 / 边界

- 没开 canonical，也没做反事实。gate 打开后是否真会产出 MOVE，只按上述静态条件推断，**不是实验验证**。
- 没有逐地点 `observe`；其它地点的机会是 UNKNOWN，不是“没有”。
- 没有执行 MOVE/POSE/REST，没有写位置、canonical 或 current_place。
- 单进程、单轮、单地点（home）；不推其它时刻/地点，不归因模型动机，不推“整个世界没有移动”。
- 目标 refs 按 ID 回查 AG-0；层③ source/target 不是最终 payload 直接记录。
- 本次为纯诊断清单，不含修复方案；未改 components/scripts/tests/旧 driver，未开真实 run，历史预算 148/300 不变。
