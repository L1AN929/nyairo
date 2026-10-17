# W01 回执：通用世界导航与模型工具适配

- 施工单：`D:\NYAIRO-v0.3-后端任务单.md`（W01）
- 交付树：`D:\nyairo-v03-backend`（独立候选 clone，新建）
- 工作分支：`work/v03-w01-navigation`
- HEAD：`7f09d26df64e054faf51ba35ad85c64f73a5a837`
- 状态：已实现 + 已离线测试；**未 commit、未 push、未部署、未调用真实模型**
- 验收环境：WSL Ubuntu + Python 3.12.3（POSIX `fcntl`、tzdata 齐备）；`PYTHONDONTWRITEBYTECODE=1`

## 1. 基线与候选树建立

| 项 | 值 |
| --- | --- |
| 只读源 clone | `C:\Users\<user>\.pi-desktop\scratch\966ed2b0-27ad-4c2c-a4b3-9021a8121e37\nyairo-v02-review`（未改动） |
| 源 clone 形态 | shallow，浅边界两个 commit：`89ecd65`(rc3) 与 `7f09d26`(main, FETCH_HEAD) |
| GitHub 远端 | `git ls-remote` 只有 `HEAD=89ecd653…`(rc3) 与 `refs/tags/v0.2.0-rc3`；**没有 `main` 分支**，无法从远端取 7f09d26 |
| 候选树建立 | `git clone --no-hardlinks --no-checkout <源> D:\nyairo-v03-backend` → 从源对象库复制对象（只读操作）→ `checkout -b work/v03-w01-navigation 7f09d26df64e054faf51ba35ad85c64f73a5a837` |
| SHA 核对 | 候选树 HEAD 与任务单/源 FETCH_HEAD 完全一致：`7f09d26df64e054faf51ba35ad85c64f73a5a837` |

候选树内无根级 `AGENTS.md`（只有 `vendor/hermes/**` 下的，属 vendor，未触碰）。

**改动前基线（候选树 @7f09d26，改动前实测）**

| 命令 | 结果 |
| --- | --- |
| `python3 -m unittest discover -s tests -p test_world_move_tool.py` | 16 tests / OK / exit 0 |
| `python3 -m unittest components.worldgen.tests.test_v04_navigation components.worldgen.tests.test_edited_world_runtime components.worldgen.tests.test_runtime_wiring` | 24 tests / OK / exit 0 |

## 2. 修改文件

```
167  38  chiyo_bundle/world_move_tool.py
  9   4  components/world/scripts/world_living_skeleton.py
 76   0  components/world/scripts/world_runtime_data.py
(新增)  tests/test_world_move_tool_navigation.py  508 行 / 14 项测试
```

### 2.1 `components/world/scripts/world_runtime_data.py`（+76，纯新增）

`WorldData` 增加三个导航能力，全部只读、无第二权威：

- `canonical_node(node) -> Optional[str]`：节点规范化。`place/entrance` 与 `place` 同义
  （沿用 `state_node` 语义），非法节点返回 `None`。
- `all_nodes() -> list[str]`：全部合法导航节点（地点 + 已声明室内区域），稳定排序。
- `route_to(origin, destination) -> Optional[list[str]]`：**世界图上的确定性最短路线**。
  返回 `origin` 之后依次经过的节点（含终点）；同节点返回 `[]`；不可达/节点非法返回 `None`。
- `_edge_seconds(origin, destination)`：边长取自既有 `route_duration_seconds`；缺耗时、
  bool 或负耗时视为**不可通行**，不会为"能走"发明耗时。

### 2.2 `components/world/scripts/world_living_skeleton.py`（+9 / −4）

- `location_catalog(environment=None)`：新增 `environment` 形参，目录改为
  `wrd.active_world(environment).location_catalog()`。此前它固定用进程环境，是"目标目录来自
  A 世界、提交落在 B 世界"的入口之一。零参调用（`world_body_gateway_api`、`world_action_resolver`
  等）行为不变。
- `apply_move_to()` 末尾 `after = _before_summary(loaded)` → `_before_summary(loaded, env)`：
  **实读调用链发现的第二个环境泄漏**（`_before_summary` 的第二参默认 `None` → 回落
  `os.environ`）。移动提交成功后的 after 摘要原本会去读进程环境的 `CHIYO_WORLD_PACKAGE`，
  进程环境指向坏包时整段提交被吞成 `WORLD_WRITE_FAILED`。该缺陷由新增测试
  `test_request_environment_wins_over_process_environment` 直接复现并锁定。

### 2.3 `chiyo_bundle/world_move_tool.py`（+167 / −38）

- **工具说明**：从"在汐见市移动到指定地点"改为"在当前活动世界的地图中移动到指定地点或
  室内区域"，说明支持 `location_id` / `place/area` / 当前世界唯一名称、按世界导航图选最短路、
  计时开启时每次只出发一段、在途不改向。未宣称任何家具交互。
- **活动世界解析**：`_runtime_data()` 新增；`move_handler` 用
  `_runtime_data().active_world(env)`（**本次请求环境**）解析目标与选路，不再用
  `wls.location_catalog()`（无参 → 进程环境）。
- **目标解析**（`_resolve_target` + `_target_index`），顺序固定：
  1. 标准节点 ID（含 `place/area`，入口区 `place/entrance` 规范化为 `place`）；
  2. 大小写不敏感的节点 ID（`" HOME "` 仍可用）；
  3. 当前世界内唯一名称：地点名、`地点·区域` 节点名、区域名、区域 ID；
  4. 汐见市旧中文别名（`_ALIASES`），**仅作最后回退**，且目标节点必须存在于当前世界。
  同名多解 → `AMBIGUOUS_LOCATION`；无解 → `UNKNOWN_LOCATION`。旧别名在自定义世界里永远
  不会压过同名真实目标。
- **选路**：`route = world.route_to(current, target)`；`None`/空 → `LOCATION_NOT_REACHABLE`
  （附 `requested_location_id`、`from_location_id`），不会偷偷落到默认家。
- **真实步行计时开启**：只提交 `route[0]` 一段；owner 返回 travel 记录 → `status="started"`，
  带 `destination_location_id`（本段）、`requested_location_id`（最终目标）、
  `estimated_minutes`、`route`（完整计划）。在途时先经 `observe_travel` 短路返回
  `in_progress`，**不调用 owner、不改向**。
- **真实步行计时关闭**：逐段提交，每段都过 owner 校验；中途非 `applied` 立即停止，
  结果带 `committed_route`（已确认的段）与 `location_id`（最后确认位置），不做补偿写入。
- **JSON 结构兼容**：保留 `ok/status/message` 与既有字段（`destination_location_id`、
  `requested_location_id`、`estimated_minutes`、`route`、`location_id`、`remaining_minutes`、
  `reason_code`、`error_type`）；新增字段只有 `committed_route`、`from_location_id`。
- `_label(location_id, world=None)`：汐见市旧短名优先（`便利店`/`家` 等消息文本不变），
  其次当前世界真实名称，最后回落到节点 ID。`travel_context` 一并按请求环境取世界名。
- `intended_target.json` 行为**原样保留**（中转时写目标、直达时清除），未扩展、未新增写者。

## 3. API 与选路语义（W02 直接消费）

```python
WorldData.route_to(origin: str, destination: str) -> list[str] | None
#   返回 origin 之后依次经过的节点（含终点）；同节点 []；不可达/非法 None
WorldData.canonical_node(node: str) -> str | None      # place/entrance → place
WorldData.all_nodes() -> list[str]                     # 地点 + 室内区域，稳定排序
world_living_skeleton.location_catalog(environment=None)
```

选路语义（已由测试固定）：

1. 最优 = 总耗时最小，边长 = `route_duration_seconds`（非负整数）；
2. 等成本时按**节点序列字典序**取最小（例：`home→s1→s2` 与 `home→s1→s3→s2` 同成本时取前者）；
3. 缺耗时/负耗时/布尔耗时的边视为不可通行；
4. 只考虑简单路径（非负边下重复节点不会更优）；
5. 同一世界、同一对节点，多次调用结果完全一致；
6. 室内与室外同图：进非入口区域必须先到该建筑入口节点（`route[-2] == place`）。

工具结果语义：

| 场景 | `status` | 关键字段 |
| --- | --- | --- |
| 目标 == 当前 | `already_there` | `location_id` |
| 即时模式逐段走完 | `arrived` | `location_id`=目标，`route`=全路径 |
| 即时模式中途被 owner 拒绝 | `rejected`/`failed` 等（透传 owner status） | `reason_code`、`committed_route`、`location_id`=最后确认位置 |
| 计时开启、第一段已出发 | `started` | `destination_location_id`=第一段、`requested_location_id`=目标、`estimated_minutes`、`route` |
| 在途中再次调用 | `in_progress` | `destination_location_id`=在途目标、`remaining_minutes`；**owner 未被调用** |
| 名称歧义 | `rejected` + `AMBIGUOUS_LOCATION` | 无位移 |
| 名称/节点未知 | `rejected` + `UNKNOWN_LOCATION` | 无位移 |
| 不可达 | `rejected` + `LOCATION_NOT_REACHABLE` | 无位移 |

所有移动仍然只经 `world_living_skeleton.apply_move_to`（现有 owner，持 `.move.lock` 并做
后置条件校验）；工具自身不写 `world_state.json`。即时模式三段路线 = 3 次 owner 提交 =
`revision +3`；计时模式只提交第一段，`world_state` revision 不变。

## 4. 命令 / 退出码 / 计数（全部实际执行）

工作目录 `/mnt/d/nyairo-v03-backend`（`D:\nyairo-v03-backend`），
`PYTHONDONTWRITEBYTECODE=1`，`PI_SCRATCH_DIR=<会话 scratch>`。

| # | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| 1 | `python3 -m unittest discover -s tests -p test_world_move_tool.py` | 16 tests / OK | 0 |
| 2 | `python3 -m unittest discover -s tests -p test_world_move_tool_navigation.py` | 14 tests / OK | 0 |
| 3 | `python3 -m unittest components.worldgen.tests.test_v04_navigation components.worldgen.tests.test_edited_world_runtime components.worldgen.tests.test_runtime_wiring` | 24 tests / OK | 0 |
| 4 | `cd components/world/service && python3 -m unittest discover -s . -p 'test_*.py'` | 259 tests / OK | 0 |
| 5 | `python3 -m unittest discover -s tests -p 'test_*.py'`（整仓 tests/） | 104 tests / FAILED(errors=9) | 1 |
| 6 | 改动前基线（候选树 @7f09d26）：#1 | 16 tests / OK | 0 |
| 7 | 改动前基线（候选树 @7f09d26）：#3 | 24 tests / OK | 0 |

第 5 项的 9 个 error **全部**是 `test_update.UpdateTransactions.*`，原因是本机未执行
`bash scripts/install.sh` 安装依赖（`ValueError: 需要安装依赖后再更新`），与本次改动无关；
同一组 error 在改动前的基线副本上同样出现（见下）。除这 9 项外，整仓 `tests/` 全绿。

## 5. 负对照证据

负对照使用**额外 scratch 副本**，未回滚候选树、未覆盖任何用户文件：

- 副本路径：`<会话 scratch>\w01-baseline`（`robocopy /E /XD .git vendor`，排除 `vendor/` 与 `.git`）
- 副本中三个产品文件用 `git show HEAD:<path>` **逐字节**还原为 7f09d26 基线
  （sha256 与 HEAD blob 一致：`world_move_tool.py 4a1a33f3…`、`world_runtime_data.py 993852b3…`、
  `world_living_skeleton.py 2453afa1…`）
- 测试文件保持与候选树一致（含新增 14 项）

| 命令（在副本内） | 结果 |
| --- | --- |
| `python3 -m unittest discover -s tests -p test_world_move_tool_navigation.py` | **FAILED (failures=4, errors=9) / 14 项** |
| `python3 -m unittest discover -s tests -p test_world_move_tool.py` | 16 tests / OK（证明副本可运行、旧保障未变） |

基线失败的 13 项（唯一在基线也通过的是纯 fail-closed 的
`test_unknown_and_invalid_targets_never_fall_back_to_home`）：

- FAIL：`test_instant_multi_leg_move_commits_every_leg_through_the_owner`
  （基线提交 `['nearby_street']` —— 固定汐见市街道中转，而非世界图选路 `['beach','bookstore','convenience_store']`）、
  `test_timed_move_submits_only_the_first_leg_and_keeps_origin`（同上）、
  `test_instant_move_stops_at_first_owner_rejection_and_keeps_position`、
  `test_request_environment_wins_over_process_environment`
  （基线 `WORLD_MOVE_ERROR / WorldDataError`：目标目录取自进程环境）
- ERROR：`test_route_is_shortest_deterministic_and_tie_broken_by_node_order`、
  `test_ambiguous_name_is_refused_without_moving`、`test_unique_name_beats_legacy_alias_in_custom_world`、
  `test_legacy_alias_still_works_and_never_crosses_worlds`、`test_timed_move_in_progress_refuses_redirect_and_keeps_travel`、
  `test_timed_move_owner_rejection_stops_immediately`、`test_entrance_area_is_normalised_to_the_place_node`、
  `test_indoor_area_move_goes_through_the_entrance`、`test_unknown_area_and_disconnected_area_are_refused`
  （基线无 `WorldData.route_to` / `canonical_node` / `all_nodes`，且名称解析与室内节点不支持）

副本整仓 `tests/` 对比（`diff` 失败清单）：候选树 9 个 error（全为 `test_update`）
是副本 24 个 error 的**子集**；副本多出的部分来自 `vendor/` 被排除导致的导入失败
（`test_context_safety`、`test_platform_scope` 等），不是产品代码差异。

## 6. 新增测试覆盖（`tests/test_world_move_tool_navigation.py`，14 项）

夹具：用 worldgen 生成世界（`make_valid_docs(seed=929711, theme="coastal_town")`）后把道路图
改写为可控小图 `home-s1(300) / s1-s2(200) / s1-s3(100) / s2-s3(100) / s2-s4(150)`，
其余地点单边挂 `s4(900)`；`s1` 命名为"公园"（与旧别名同名），`s2` 与一个死胡同地点同名
"旧市场"；室内夹具另跑 `apply_interiors_to_package` 取得真实区域图。

| 验收点 | 测试 |
| --- | --- |
| 自定义非汐见市地点 | 全部自定义世界用例（`nearby_street` 不存在） |
| ≥3 段路线 | `test_instant_multi_leg_move_commits_every_leg_through_the_owner`、`test_route_is_shortest_…` |
| 含环图 | `test_route_is_shortest_…`（`s1-s2-s3-s1` 三边） |
| 等成本稳定选路 | `test_route_is_shortest_…`（两条同成本路径，断言取字典序最小，且正反向都稳定） |
| 不可达 | `test_unknown_area_and_disconnected_area_are_refused`（断开区域连接后拒绝） |
| 未知名称 / 非法输入 | `test_unknown_and_invalid_targets_never_fall_back_to_home` |
| 歧义名称 | `test_ambiguous_name_is_refused_without_moving`（两个同名节点，拒绝且零位移） |
| 入口区规范化 | `test_entrance_area_is_normalised_to_the_place_node` |
| 室内区域移动 | `test_indoor_area_move_goes_through_the_entrance`（含逐跳连通性断言） |
| 请求 environment ≠ 进程 environment | `test_request_environment_wins_over_process_environment` |
| 在途不改向 | `test_timed_move_in_progress_refuses_redirect_and_keeps_travel` |
| 中途 owner 拒绝后不续走 | `test_timed_move_owner_rejection_stops_immediately`、`test_instant_move_stops_at_first_owner_rejection_and_keeps_position` |
| 老别名兼容 + 不同世界不串扰 | `test_legacy_alias_still_works_and_never_crosses_worlds`、`test_unique_name_beats_legacy_alias_in_custom_world` |
| 仍经 owner、不直接写 world_state | `test_instant_multi_leg_…`（revision +3）、`test_timed_move_…`（revision 不变） |

## 7. 未完成项 / 已知限制

1. **未提交、未推送、未部署**；分支 `work/v03-w01-navigation` 上的改动保持未 staged 状态
   （3 改 + 1 新增测试 + 本文档）。
2. 计时模式下工具**只保证第一段**：到达中转点后是否续行由下一次调用决定，本单未实现自动
   续行，也未声称。W02 负责持久旅程与恢复。
3. `intended_target.json` 的既有写者行为原样保留（仍是无锁原型，仅 `scripts/life_heartbeat.py`
   消费）；W01 没有扩展它，也没有把它接进新的调度路径。
4. `world_body_gateway_api.py:456`、`world_action_resolver.py:455` 仍以零参调用
   `location_catalog()`（即进程环境）。它们不在模型工具链路上，属 W03 的"统一 owner/装配入口"
   范围，本单只把该 API 改成可接收 environment，未改这两个调用点。
5. `world_travel_delivery._current_activity_status()` 用 `os.environ["XINXI_DIR"]`（进程级），
   是既有的另一处环境回落，只在计时模式下参与 `plan_travel`；本单未改（属旅行/调度面）。
6. 原生 Windows 仍不可跑世界移动链路（缺 `fcntl` 与 tzdata）；新测试带 `os.name == "nt"` 跳过
   标记，与既有 worldgen 测试一致。未修改全局 Python。
7. 未做真实模型调用、未动真实 profile / 密钥 / 供应商设置；未做长测或线上验证。
8. 未执行：`scripts/install.sh` 依赖安装（因此 `test_update` 9 项保持既有 error）、
   平台全量 CI、打包/发布校验。

## 8. W02 所需接口

- 路线与图：`WorldData.route_to(origin, destination)`（返回含终点的段序列）、`canonical_node`、
  `all_nodes`；语义见 §3，已由测试固定，可直接作为持久旅程的"完整路径"来源。
- 提交：仍只经 `world_living_skeleton.apply_move_to(location_id, hermes_home=…, environment=…,
  now=…, execution_id=…)`；单段、持锁、带后置条件。多段续行必须自己逐段提交并读回位置。
- 计时状态：`world_travel_delivery.plan_travel / start_travel / observe_travel /
  realize_due_travel`，均接受 `environment`；工具返回的 `route` 可直接持久化，`destination_location_id`
  标识当前段。
- 活动世界：一律用 `world_runtime_data.active_world(environment)` / `package_root(environment)`；
  `world_living_skeleton.location_catalog(environment)` 已可用。**不要**再使用任何零参回落。
- 环境泄漏提醒：`_before_summary(state, environment)` 必须显式传环境（本单已修一处；
  `_current_activity_status`、gateway/resolver 调用点仍是待统一项）。
- 结果字段约定：`status ∈ {arrived, started, in_progress, already_there, rejected, failed,
  unavailable, disabled}`；失败结果带 `reason_code`、`committed_route`、`location_id`（最后确认位置）。

## 9. 范围外写入

- 候选树内：只有 §2 的 3 个产品文件 + 1 个新增测试文件 + 本文档；未触碰 `vendor/`、`website/`、
  `config/`、`scripts/`、`patches/` 等。
- 候选树外：只写会话 scratch（`w01-baseline/` 负对照副本、`w01_debug_env*.py`、`w01_*.sh`、
  测试临时世界与实例）；未写只读源 clone（仅复制其对象），未覆盖任何已存在的未知路径。
- 未删除任何文件，未 `reset`/`checkout` 任何未知改动，未创建远程 PR。
