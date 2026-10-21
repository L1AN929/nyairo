# Agent Note: 拿放从 Life 决策到 World owner 的唯一通路，以及有界生活现场（v0.4 B）

Status: implemented

## Problem

World owner 早就有两个真实的"手"动作：`PICK_UP {object_id, slot_id}`
（`world_action_resolver:1198`）与 `PLACE {object_id, target}`（`world_action_resolver:1334`），
`action_intent.EXPOSED_VERBS` / `ACTION_ALLOWLIST` 也早就登记了它们。但 Life 侧三处缺口让
"她真的把杯子拿起来放到桌上"无法发生：

1. **没有 target 拼写**。AR-0 一次动作只有一个 `target` 字符串，`place:`/`object:`/`stay:`/
   `bodyact:` 已经各自占用命名空间；这两个 verb 一个要两个参数、另一个要一整个 placement
   映射，没有任何合法写法。于是"拿起"这条路只能靠测试直调 resolver（正是被禁止的旁路）。
2. **provider 不翻译它们**。`world_move_opportunity_source` 只翻 MOVE/CONSUME/STAY/POSE/REST，
   所以 AG-0 的机会里根本没有拿放；`get_available_actions` 里 owner 已经给出的
   `PICK_UP.candidates[{object_id, slots}]` 与 `PLACE.available` 没被用上。
3. **现场视图不是给角色看的**。`last_observation`/`last_catalog`/`last_opportunity_view`
   只服务于报告：地点是 id 而不是名字，没有"我现在手上有什么、旁边有什么能拿、能放哪"，
   模型无法知道"拿什么放哪里"，只能看见 opaque ref。

## Decision

### 一、`life_daily_action_targets.py`（新模块）：一个 target 装下全部参数

```
dailyact:pick_up:<slot_id>:<object_id>
dailyact:place:room:<location_id>:<area_id>:<object_id>
dailyact:place:surface:<location_id>:<surface_id>:<object_id>
```

- 每个字段 `urllib.parse.quote(value, safe="")`：真实 surface id 形如
  `desk_surface:<item>`，含分隔符，必须编码才能确定性往返。
- 只接受 owner 自己的参数白名单，且**必须完整**：`PICK_UP {object_id, slot_id}`、
  `PLACE {object_id, target}`；`target` 只允许 `room`/`surface`（键集与
  `world_body_substrate.validate_placement_target` 一致）。多一个键、少一个键、未知 verb、
  `container`/`hand`/`in_transit` 目的地、非白名单手槽一律抛 `DailyActionTargetError`。
- 公开四个函数（A 的写集只读它们，不再自己拼字符串）：
  `encode_daily_action_target(verb, params) -> str`、
  `parse_daily_action_target(target) -> {verb, params, target_ref, object_id} | None`、
  `build_daily_action_step(target, *, origin_ref=None) -> AG-2 step`（与
  `life_body_action_targets.build_body_action_step` 同形：`payload_spec` 仍只有
  `verb`/`target`/`expected_dependencies`）、
  `daily_action_completion(target) -> {fact_key, expected_value}`。
- 完成事实只有一处来源：`get_snapshot`。`daily_action_observed_fact(parsed, snapshot)` 给出
  `{value, satisfied, observed, unknowns}`；读不到就 `value=None`、`satisfied=False`。
  room 目的地的"哪间房"由身体当前所在推得（owner 的 destination 规则只允许当前区域），
  这一条在 `unknowns` 里标为 `room_placement_location_from_snapshot_location`，不当成直接读到的字段。

### 二、`provider.observe` 扩两 verb，`daily_life_context` 是给角色的现场

- `observed_daily_action_opportunities(...)`：`PICK_UP` 每条机会 = owner 候选里的一个物件 ×
  owner 给的一个空闲手槽（按 `HAND_SLOTS` 顺序取第一个）；`PLACE` 每条机会 = 快照 `hands`
  里真拿着的物件 × 真实目的地。目的地只有两处来源：当前 `location.place_id/area_id`，
  以及只读 gateway 命令 `get_environment` 里 `surfaces_here` 投影出的**本地点**台面。
  owner 说不 available / 没有候选 / 没有空手 / 没拿东西 → 零条机会。AG-0 record 形状不变。
- `daily_life_context(limit=8)`：只读本轮已发生的那次观测，返回 `location`（名字/区域名/营业/
  场景/当地时间，全部来自 World 自己的目录）、`body`（姿势/朝向/真拿着的手/空手/能否动手）、
  `nearby`（目录自己列的地方 + 路程 + 营业）、`nearby_objects`（真的伸手可及：手上的 +
  owner 说此地可取的）、`opportunities`（每条带名字、一句描述、时长与来源、准备事实）、
  `unknowns`（短码）+ `unknown_details`。**没有**打分、排序偏好、默认选择：排列只是展示分组
  （手边动作 → 待着 → 用掉 → 去哪里 → 姿势），组内保持观测自身顺序，`order_basis` 明说。
- 不知道就说不知道：远处没亲眼看到的地点只给时间表事实、不塞内部物件细节；快照不暴露位置的
  物件不进 `nearby_objects`；容器目的地这版 owner 不接受，所以不给。

### 三、Bridge adapter：两 verb 走真实 Resolver，成功后读回真实 placement

- `SUPPORTED_VERBS = ("MOVE","CONSUME","POSE","REST","PICK_UP","PLACE")`；`resolver_params`
  对两 verb 走 `parse_daily_action_target`，解不出是**提交前**确定拒绝
  （`DAILY_ACTION_TARGET_INVALID` + `DEFINITE_PRE_SUBMIT`），没铸授权、没碰 owner。
- 成功后 `details = {observation_source, post_state_only: True,
  post_state_proves_this_execution: False, daily_placement, placement_fact}`：
  可以核对"现在的 placement/hands 是否等于完成事实"，但**不**宣称后置状态证明了这次执行；
  归属仍只在 AR-0 回执与世界执行记录。`read_daily_placement(object_id)` 给 A 一个只读查询口。
- 幂等仍按**原 key** 缓存，UNKNOWN 仍按同一 `execution_id` 对账；四 verb 不退化。
- `RealWorldActionAdapter.validate_prepare` 只对这两个 verb 放宽（payload 仍是同样的三个键）。

## Alternatives considered

- **复用 `bodyact:` 命名空间装拿放** — 少一个前缀，A 的命名空间分流只改一处。否决：`bodyact:`
  的语义是"身体姿势/恢复会话"，把物件 id 塞进去会让"这个 target 是身体动作还是物件动作"变成
  靠位置猜；两套语义混在一个命名空间，正是本仓反复避免的"一种拼写两种意思"。
- **给 AR-0 加自由 `params` 映射** — 最省事，`PLACE` 的嵌套 destination 直接透传。否决：
  AR-0 的合同就是"一次动作一个 target"，一旦开放 params，调用方可以填任意参数，等于绕过
  `action_intent` 的准入与 fingerprint；而且"模型/调用方自己拼参数"正是要禁止的旁路。
- **让 provider 枚举所有容器/台面当目的地** — 会立刻显得"能放的地方很多"。否决：owner V1 明确
  拒绝 `container` 目的地，快照也不暴露 placement 的位置；枚举出来的列表里会有一半提交时被拒，
  等于用"看起来能选"换真实。改成只用 owner 自己投影的本地点台面 + 当前房间。
- **把报告视图（`last_opportunity_view`）直接当角色 context** — 不用新代码。否决：那份视图是
  报告用的，地点是 id、没有"手上有什么/能放哪/要多久"、还有 `world_revision_ref` 这类内部字段；
  直接塞进 prompt 既不可读也越界。现在的 `daily_life_context` 复用同一次观测，但只投影角色该知道的。
- **让 Life 侧直接读 substrate/世界库判断能不能拿** — 判断最准。否决：那是第二份 World 真相，
  `takeable`/手占用/姿势/目的地全是 owner 的判定；Life 只翻译 owner 已经给出的候选。
- **成功后把"物件在手/在地上"当成本次操作成功的证据** — 最省事。否决：后置状态无法区分"这次放的"
  和"本来就在那"；所以 details 里同时给 `satisfied` 与 `post_state_proves_this_execution: False`。

## Consequences

- **收益**：`PICK_UP`/`PLACE` 第一次能从一次真实观测出发走完整条正式链：AG-0 机会 →
  `dailyact:` target → AG-2 步骤（同形）→ AR-0 → Intent Executor 铸一次性授权 → `gateway.sock`
  → Resolver 提交 → 从 substrate/快照读回真的 placement/hands。`tests/test_world_daily_hands.py`
  用真实 `WorldBodyService`（真 lease、真授权注册表、真 substrate）证明拿起与放下都真的改变了世界。
- **收益**：角色侧第一次拿到"名字 + 手上 + 旁边 + 能放哪 + 要多久 + 未知清单"，而不是一串 id；
  未知（远处未观测、容器目的地、台面读不到、姿势动不了手）都显式列出，模型要装知道反而更难。
- **代价与已知上限**：白名单是显式的，加一个 verb 仍要动 AR-0 的 verb 集合与
  `world_journey_action_adapter.DELEGATED_VERBS`（这两处不在本包写集，回执 §5 写明归属 A）。
- **代价与已知上限**：PICK_UP 只取 owner 手槽顺序里的**第一个**空闲手，因此"双手都空时用左手"
  是确定性行为而不是模型选择；`hand` 目的地（换手）不编码，因为那不是"放下"。
- **代价与已知上限**：`HAND_MANIPULATION_SECONDS=2.0` / `POSTURE_SECONDS=5.0` 是
  `body_consequence` 的副本（该模块把 PICK_UP/PLACE 的 2.0s 直接写在分支里），owner 一改这两个
  副本要跟着改；`REST` 不给 duration，因为它是会话不是时长。
- **代价与已知上限**：`get_environment` 是每次观测多一次只读 round trip；它读不到时只损失台面
  目的地（房间仍然可用），并在 `unknowns` 里说明。

## Verification

- `tests/test_world_daily_hands.py`（新，12 用例，WSL `/root/nya-venv/bin/python -m unittest`）：
  6 个编码契约用例（确定性/可逆/拒绝越界/step 同形/完成事实只认读到的）+ 6 个真实 owner 用例
  （真拿起→读回 hands、真放下→读回 room placement、同 key 重试不改世界、拒绝与未知不伪装成功、
  现场视图名字/营业/未知边界、A 的 `life_live_context_view` 直接消费）。
  命令 exit 0。
- 同批定向回归：`tests.test_day_life_body_actions`（14 用例）与本包合跑 26/26 exit 0；
  `tests.test_world_daily_life_consume` 6/6 exit 0。`tests.test_world_daily_life_continuous` 的
  `ALREADY_DONE != ACTED` 红灯在本包三个源码文件被 `git stash` 摘掉后同样复现，属既有的
  artifact-closeout 路径问题（失败那一轮没有任何 dailyact 目标）。
- 回执与接口细节：`docs/engineering/v04-lived-day-world-result.md`。
