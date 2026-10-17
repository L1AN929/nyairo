# W 包回执：有限街区场景 + 有来源的只读机会目录

日期：2026-10-17｜分支 `work/v03-w01-navigation`｜HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`｜**全部 UNCOMMITTED**（未 stage/commit/push，未 reset/clean/checkout）。
接线上限：本包只做 World 侧读口与静态场景；**没有接 Life 核心**（candidate/选路/旅程/进食都没动），留给 I/F。

## 1. 自有 delta（只碰了这些文件）

| 文件 | 内容 |
|---|---|
| `examples/worlds/daily_neighborhood_v1/`（新） | 合法静态世界包：`world.json` / `topology.json` / `rules.json` / `npcs.json` / `ambient.json` / `objects.json` / `content.json` / `scene.json` / `MANIFEST.json`。用仓库自己的 `worldgen.schema.write_package` 生成，`validate_package_dir` 为空。 |
| `components/world/scripts/world_daily_opportunities.py`（新，391 行） | `location_opportunity_catalog`（L226）、`compact_projection`（L339）、`MAX_WALK_SECONDS=1800`（L39）、`MAX_OPPORTUNITIES=12`（L37）。内部只读 `world_runtime_data` + `world_daily_rhythm` + 包场景快照。 |
| `components/world/scripts/world_living_skeleton.py`（改） | 新增 `read_world_state()`（L280，公开“当前世界状态”只读口）；`observe_world` 末尾新增 `reachable_opportunities`（L700–L724，16KB 预算超限则只省略该字段）；`plan_move_to` 营业分支改为 `destination_availability` 提示（L843–L858，返回 L896/L905）；`apply_move_to` 同改（L994–L1009，返回 L1077/L1124）；`__all__` 加 `read_world_state`（L1252）。**删除** `LOCATION_CLOSED` / `DAILY_RHYTHM_UNAVAILABLE` 两个拒绝分支。 |
| `tests/test_world_daily_opportunities.py`（新） | 10 个定向用例（见第 5 节）。 |
| `components/worldgen/tests/test_edited_world_runtime.py`（改，收口授权） | 旧 `rejected/LOCATION_CLOSED` 断言改为可达性拒绝（`LOCATION_NOT_REACHABLE` 且显式 `assertNotEqual(..., "LOCATION_CLOSED")`）；新增独立用例 `test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty`（park↔bookstore 相邻、11 点 plan/apply 均成功 + `destination_availability == CLOSED/OUTSIDE_OPEN_HOURS` + `affordances_for_location == []`，13 点才出现 `BROWSE_STORE`）。11/13/20 营业 owner 断言原样保留。 |
| `docs/engineering/world-daily-w-usage.md`（新） | 给 F/I 的短用法（import/API/过滤/走法）。 |
| `.agents/notes/implemented/architecture/2026-10-17-world-daily-opportunities.md`（新） | 决策笔记（Problem/Decision/Alternatives/Consequences/Verification）。 |

场景数据与 F 的运行时 food 声明**没有共用**：包里的 `BUY_DRINK` 只是时间表事实，不声明库存、份量、余额、可否吃。

## 2. 场景包：路径与初始化

- 包路径：`examples/worlds/daily_neighborhood_v1/`（`world_id = daily_neighborhood_v1`，`timezone = Asia/Tokyo`）。
  派工写的 `components/world/packages/` 在仓库中不存在；仓库里所有随仓世界包都在 `examples/worlds/<world_id>/`（`components/worldgen/tests/test_edited_world_runtime.py` 直接引用其中之一），故按标准位置落地并在此报告。
- 六个地点：`home`(home/bedroom；室内区域 bedroom+living)、`street_front`、`street_corner`、`park`、`cafe`(cafe；室内 main+counter)、`reading_point`(library；室内 reading_room+shelf)。
- 五条边全为真实正秒：`home–street_front 90`、`street_front–street_corner 120`、`street_front–reading_point 150`、`street_corner–park 180`、`street_corner–cafe 120`。从家：街道 90s、街口 210s、阅读角 240s、咖啡店 330s、公园 390s。
- 营业条件：公园 6–22；咖啡店 **工作日 8–18**（`weekdays_only: true`）；阅读角 10–20；家/街道常开。
- 不需产出的停留 affordance：公园 `SIT`、阅读角 `READ_HERE`（另有 `WALK`/`LOOK_AROUND`）；咖啡店 `SIT_AT_CAFE`/`BUY_DRINK`/`LOOK_AROUND`。
- 可见物件：`scene.navigation.furniture_map` 声明 —— 卧室 `desk_home`(书桌)/`bed_home`(床)；街道 `street_lamp_front`；街口 `street_sign_corner`；公园 `bench_park`/`tree_park`；咖啡店 `table_cafe`/`chair_cafe`/`counter_cafe`；阅读角 `reading_table`/`shelf_reading`。户外地点也用 `navigation` 声明区域，因为运行时可见物件只从这里来（`objects.json` 不参与可见家具投影）。
- 实例初始化（真实命令，已实测 exit 0）：

```
cd <repo>
python -m components.worldgen instance --package examples/worlds/daily_neighborhood_v1 --home <新的隔离实例目录>
# 返回 {"status":"created","initial_location":"home/bedroom",...}
# 运行时：CHIYO_WORLD_PACKAGE=<包目录> CHIYO_WORLD_HOME=<实例目录>
```

## 3. 真实 API（F/I 接线用）

```python
import world_daily_opportunities as wdo
catalog = wdo.location_opportunity_catalog(environment, now, hermes_home=world_home)
```

`environment`/`now` 可位置传参，`hermes_home` 关键字；`now=None` 取当前时刻（必须 aware 才显式传）。
条目字段：`location_id`、`name`、`kind`、`here`、`reachability{status,route,walk_seconds,source}`、`availability{status,reason_code,source}`、`affordances[{affordance_id,description,source,action_grant:false}]`、`observed{status,...}`、`indoor_areas`、`time{local_time,local_date,weekday}`、`validity{as_of,scope:"NOW_ONLY",future:"not_promised"}`、`action_grant:false`。
顶层：`status`（available/unavailable + `reason_code` 之一：`WORLD_NOT_INITIALIZED`/`WORLD_PACKAGE_UNREADABLE`/`WORLD_DATA_UNAVAILABLE`/`WORLD_LOCATION_INVALID`）、`world_id`、`timezone`、`local_time/local_date/weekday`、`current_location_id`、`current_place_id`、`home_location_id`、`schedule_source`（`world_package_schedule` 或 `world_daily_rhythm_builtin`）、`observed_scope:"current_location_only"`、`validity`、`opportunities`、`truncated`、`action_grant:false`、`note`。

`observe_world` 里同名短视图（`reachable_opportunities`）每项只有：
`location_id/name/kind/here/walk_seconds/reach/availability/availability_reason/affordance_ids/observed/action_grant`。

**真实样例（2026-10-16 周五 13:00 JST，实例在 home/bedroom）**：

```
schedule_source=world_package_schedule  current_location_id=home
home           here       0s   OPEN   ALWAYS_AVAILABLE      [REST,READ,USE_PHONE]  visible_here
street_front   reachable 90s   OPEN   ALWAYS_AVAILABLE      [WALK,LOOK_AROUND]     not_observed
street_corner  reachable 210s  OPEN   ALWAYS_AVAILABLE      [WALK,LOOK_AROUND]     not_observed
reading_point  reachable 240s  OPEN   WITHIN_OPEN_HOURS     [BROWSE_BOOKS,READ_HERE] not_observed
cafe           reachable 330s  OPEN   WITHIN_OPEN_HOURS     [SIT_AT_CAFE,BUY_DRINK,LOOK_AROUND] not_observed
park           reachable 390s  OPEN   WITHIN_OPEN_HOURS     [WALK,SIT,LOOK_AROUND] not_observed
```

同一天的营业边界（真实输出）：`07:59 CLOSED/OUTSIDE_OPEN_HOURS → 08:00 OPEN → 17:59 OPEN → 18:00 CLOSED`；`周六 13:00 CLOSED/WEEKEND_CLOSED`；`周一 09:00 OPEN`。跨日：`周五 23:59 → 周六 00:01` 时 `local_date/weekday` 翻页，咖啡店原因从 `OUTSIDE_OPEN_HOURS` 变成 `WEEKEND_CLOSED`，公园两侧都 `CLOSED`（6–22）。

## 4. 已实现但未接线（本包边界内）

- 静态场景包已可被 `world_runtime_data` / `worldgen` 直接加载；但**没有任何 Life/旅程文件引用它**，也没有新增启动参数或默认世界切换。
- 机会目录只被 `observe_world` 附带使用；**未接** candidate 源、未接 Resolver、未改 `chiyo_bundle/world_move_tool.py`。
- 咖啡店消费、公园停留的“执行”没有做：`BUY_DRINK` 只是时间表 affordance，没有库存/份量/余额/支付。
- `MOVE` 语义改变已生效（闭店可到门口），但**未**在 move tool/旅程里加“等开门/门口折返”的策略——那是 I 的选择层。

## 5. 测试与校验（真实 exit）

WSL Python：`S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37`，`$S/w04venv/bin/python`，`PYTHONDONTWRITEBYTECODE=1`，cwd 为 repo。

| 命令 | 结果 |
|---|---|
| `python -m unittest tests.test_world_daily_opportunities` | **Ran 10, OK, explicit exit 0** |
| `python -m unittest tests.test_world_move_tool tests.test_world_move_tool_navigation components.worldgen.tests.test_v04_navigation components.worldgen.tests.test_runtime_wiring` | **Ran 50, OK, explicit exit 0** |
| `python -m unittest tests.test_world_journey components.worldgen.tests.test_edited_world_runtime` | Ran 32，**1 failure**：`test_edited_world_runtime.py:139` 断言旧语义（旧失败记录保留，已在收口更正，见第 8 节） |
| `python -m unittest tests.test_life_v1_integration` | Ran 56，**7 errors**，全部是 `world_body_gateway_api.py:647 SyntaxError`（F 正在编辑该文件）；其余 49 通过 |
| `node --experimental-strip-types …/verify-agent-note-tree.ts` / `verify-agent-note-format.ts`（repo cwd） | 两个都 `ok: 13 note(s)…`，exit 0 |

自有 10 个用例（精确 ID）：`test_neighborhood_package_is_valid_and_loads`、`test_worldgen_init_instance_really_creates_the_neighborhood`、`test_observe_projects_opportunities_with_real_competition`、`test_open_hour_boundary_and_weekday_flags_follow_owner_semantics`、`test_cross_day_boundary_uses_owner_local_date`、`test_closed_cafe_door_is_walkable_without_store_affordances`、`test_closed_door_is_allowed_in_timed_travel_too`、`test_unknown_and_unreadable_report_reasons_without_fake_offers`、`test_observation_is_read_only`、`test_walk_seconds_are_real_positive_route_sums`。

负例与恢复：
- 未初始化实例 → `unavailable/WORLD_NOT_INITIALIZED`，`opportunities == []`；包路径不存在 → `unavailable/WORLD_PACKAGE_UNREADABLE`；把 `wdr.location_availability` 打成异常 → 该地点 `UNKNOWN/DAILY_RHYTHM_UNAVAILABLE`、affordances 为空、`action_grant false`，目录本身仍 `available`（不伪装成“开着/关着”）。
- 观察只读：`observe_world` + 目录前后对实例目录做 sha256 快照对比完全一致，`life_execution.jsonl` 仍 0 字节，`world_revision` 不变，输出里没有 `candidates`/`model_calls`。
- 恢复：本包不写状态，异常路径只返回原因码；重跑上述命令即可复原结论，测试实例目录留在 `/tmp/nyaw05r.jdK3q/w-daily-*`（未清理）。

## 6. 真剩余限制（需要别人决定/后续）

1. **旧验收与本次决定正面冲突（已在本轮收口更正，见第 8 节）**：`components/worldgen/tests/test_edited_world_runtime.py::test_edited_opening_hours_actually_govern`（原第 134–139 行）断言“11 点去书店 → `rejected/LOCATION_CLOSED`”。当时红灯的实际结果是 `LOCATION_NOT_REACHABLE`（书店与家不相邻，单段 MOVE 本来就到不了），因为营业不再拦出发。父级已裁定“MOVE 可以到闭店门口、营业限制场内机会、不回退”，并授权本包独占修改该测试文件；更正方式与证据见第 8 节。
2. `tests.test_life_v1_integration` 当前红是 F 在改 `components/world/service/world_body_gateway_api.py`（`SyntaxError: closing parenthesis ']' does not match opening '{' on line 627`，`line 647`），与本包无关；F 收口后需重跑。
3. `observe_world` 的 16KB 预算：包世界现在约 5.9KB（含短视图），整镇生成世界可能触发省略 `reachable_opportunities`——这种时候调用方要拿全量必须直接调目录，短视图不是保证字段。
4. `MAX_OPPORTUNITIES=12` / `MAX_WALK_SECONDS=1800` 是模块常量，街区变大要么改常量要么改成配置；室内区域节点不出现在条目里（只给 `indoor_areas` 提示），到“沙发上坐下”这类区域级动作仍由 Resolver/动作 owner 决定。
5. 户外地点用 `scene.navigation` 暴露可见物件是对既有 schema 的扩张使用（validator 允许，注释写的是建筑内部）；未来若给户外单开声明，这份包要跟着改。
6. 单段 `MOVE` 仍是单边一跳：多段行程靠 move tool 逐段提交，“走到一半改主意”属 I 的选择层，本包只提供 route/秒数事实。

## 7. 工作区状态

`git status --porcelain`（本包相关，全部 UNCOMMITTED，未 stage/commit/push）：

```
 M components/world/scripts/world_living_skeleton.py
 M components/worldgen/tests/test_edited_world_runtime.py
?? components/world/scripts/world_daily_opportunities.py
?? examples/worlds/daily_neighborhood_v1/
?? tests/test_world_daily_opportunities.py
?? docs/engineering/world-daily-w-usage.md
?? docs/engineering/world-daily-w-result.md
?? .agents/notes/implemented/architecture/2026-10-17-world-daily-opportunities.md
```

同仓其它改动（F 的 `docs/engineering/world-daily-f-result.md`、life/world service 等）不是本包的，未触碰、未回滚。

## 8. 更正（收口：闭店门口语义的旧验收）

父级裁定：**MOVE 可以到闭店门口，营业限制场内机会，不回退**。据此独占修改了受影响的
`components/worldgen/tests/test_edited_world_runtime.py`（只改这一条测试文件的旧断言并新增一条精确用例，
未碰产品代码、未碰 F/Life 文件；用例仍用仓库真实 `examples/worlds/nagiura-edited` 与真实 `init_instance` 实例）。

改了什么（不是把原因码换个字符串了事）：

- **保留** 11/13/20 三条营业 owner 断言（`wdr.location_availability` 的 CLOSED / OPEN+`WITHIN_OPEN_HOURS` / CLOSED）。
- 原来“11 点去书店 → `rejected/LOCATION_CLOSED`”改为：从 home/bedroom 出发书店**本身不相邻**，拒绝原因只能是
  `LOCATION_NOT_REACHABLE`，并显式 `assertNotEqual(reason, "LOCATION_CLOSED")`，防止再用营业冒充拓扑事实。
- **新增独立用例** `test_closed_bookstore_doorstep_is_reachable_but_store_affordances_are_empty`（真实邻近闭店对：
  `park↔bookstore 199s`，书店 12–18）：
  - 10 点即时模式 `home→street_1→park`，两次 `apply_move_to` 均 `applied`，位置真实到 `park`；
  - 11 点闭店：`plan_move_to("bookstore")` 为 **ready / REACHABLE**，`destination_availability` 为
    **CLOSED / OUTSIDE_OPEN_HOURS**；`wdr.affordances_for_location("bookstore", 11点) == []`；
  - 11 点 `apply_move_to("bookstore")` 真的提交（`applied / MOVE_COMMITTED`），位置到 `bookstore`，闭店提示跟着结果走，
    店内 affordance 仍为空；
  - 13 点开门：同一个门口才出现 `["BROWSE_STORE"]`。
- 模块 docstring 增一条“闭店只约束店内 affordance，不拦走到门口（已裁定）”。

定向复跑（WSL `$S/w04venv/bin/python`，`PYTHONDONTWRITEBYTECODE=1`，cwd=repo）：

| 命令 | 结果 |
|---|---|
| `python -m unittest components.worldgen.tests.test_edited_world_runtime` | **Ran 5, OK, exit 0**（含新用例；原红灯消失） |
| `python -m unittest components.worldgen.tests.test_edited_world_runtime components.worldgen.tests.test_v04_navigation components.worldgen.tests.test_runtime_wiring tests.test_world_daily_opportunities tests.test_world_move_tool tests.test_world_move_tool_navigation` | **Ran 65, OK, exit 0** |
| `node --experimental-strip-types …/verify-agent-note-tree.ts` / `verify-agent-note-format.ts` | 两个 exit 0（见下） |

未跑、也未重跑：`tests.test_life_v1_integration`（F 正在编辑 `world_body_gateway_api.py`，与本更无关）、全仓/World 全套。
产品代码本轮未改；定向复跑没有暴露需要修的 W 缺陷。旧失败记录保留在第 5/6 节，作为更正前的历史。
