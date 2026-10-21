# B 包回执（v0.4 世界日常）：她实际的"拿起/放下"与有界生活现场

- 工作区：`D:\nyairo-rc1-body-backend`（WSL `/mnt/d/nyairo-rc1-body-backend`），branch
  `work/rc1-phone-world-body`，HEAD `41e818faaa24115fbf43db2977b15585c04144af`（未移动）。
- 状态：**UNCOMMITTED**。未 stage/commit/push/reset/clean/checkout；他人既有 dirty 原样保留。
- 写集（独占）：`components/life/scripts/life_daily_action_targets.py`（新）、
  `components/life/scripts/world_move_opportunity_source.py`、
  `components/life/scripts/world_action_bridge_adapter.py`；外加本包自己的测试、note、本回执。
  **没有**改 `action_intent.py`（PICK_UP/PLACE 早就登记在 `EXPOSED_VERBS`/`ACTION_ALLOWLIST` 里）、
  A 的 cycle/assembly/cognition、C 的 resident/runner/entry，也没有改任何 World owner 文件。
- 未做：真实模型/联网、24h、生产部署、凭据、commit/push、子委派；没有删除任何文件。
- 时钟：真实墙钟（世界服务自己的 clock），未注入假时钟。

---

## 1. 给 A 的稳定接口（实际签名，按本文件为准）

### 1.1 `life_daily_action_targets.py`（新）

| 函数 | 实际签名 | 返回 |
|---|---|---|
| `encode_daily_action_target` | `(verb: Any, params: Any) -> str` | 确定性 target 字符串 |
| `parse_daily_action_target` | `(target: Any) -> Optional[dict]` | `{verb, params, target_ref, object_id}`；别的命名空间 `None`，本命名空间畸形抛 `DailyActionTargetError` |
| `build_daily_action_step` | `(target: Any, *, origin_ref: Any = None) -> dict` | 与 `life_body_action_targets.build_body_action_step` **同形**的 AG-2 step |
| `daily_action_completion` | `(target: Any) -> dict` | `{fact_key, expected_value}` |
| `daily_action_observed_fact` | `(parsed: Any, snapshot: Any) -> dict` | `{fact_key, expected_value, value, satisfied, source, observed, unknowns}` |
| 附加（只读辅助） | `is_daily_action_target(ref)`, `daily_action_effect_predicates(parsed)` | — |

**编码形状**（每个字段 percent-encode，所以含 `:` 的真实 surface id 也能原样往返）：

```
dailyact:pick_up:<slot_id>:<object_id>
dailyact:place:room:<location_id>:<area_id>:<object_id>
dailyact:place:surface:<location_id>:<surface_id>:<object_id>
```

- 只接受 owner 实际暴露的参数：`PICK_UP {object_id, slot_id}`、`PLACE {object_id, target}`
  （`target` 只允许 `room`/`surface`，键集与 `world_body_substrate.validate_placement_target` 一致）。
  多一个键、少一个键、未知 verb、`container`/`hand`/`in_transit` 目的地、非白名单手槽一律抛错，
  **不会**产出"看起来能选、提交时才被打回"的 target。
- `build_daily_action_step` 的 `payload_spec` 仍只有 `verb`/`target`/`expected_dependencies` 三个键，
  `expected_effect_predicates` 是 `world_object_picked_up` / `world_object_placed`，
  `idempotency_key = idem_step:v02:world:dailyact:<quote(origin_ref) or unknown>:<target>`。
  `origin_ref` 由 A 自己给（例如 `"place:home@r12"` 或 perception ref），只进幂等键。
- `daily_action_completion` 的 fact（LR-5 既有 `world_fact:` 命名空间，key `world_daily_placement_ref`）：
  `world_fact:placement:<object>:hand:<slot>` /
  `world_fact:placement:<object>:room:<location>:<area>` /
  `world_fact:placement:<object>:surface:<surface>`。
- `daily_action_observed_fact` 只认 `get_snapshot`：值读不到就是 `None`、`satisfied: False`；
  room 目的地的"哪间房"由**身体当前所在**推得（owner 的 destination 规则只允许当前区域），
  这一条在 `unknowns` 里显式标成 `room_placement_location_from_snapshot_location`，不当成直接读到的字段。

### 1.2 `provider.daily_life_context(limit: int = 8) -> dict`（位置/关键字都能传）

```python
{
  "status": "available" | "unavailable",
  "reason_code": None | "NO_OBSERVATION" | ...,
  "observed_at": "<ISO>" | None,
  "location": {"place_id", "area_id", "name", "area_name", "kind", "here",
               "indoor", "scene_id", "local_time", "timezone",
               "availability", "observes"},
  "body": {"pose", "orientation",
           "hands": [{"slot", "object_id"}...],   # 只列真拿着东西的手
           "hands_known": bool, "free_slots": [...], "holding": [...],
           "hands_usable": bool | None},
  "nearby": [{"place_id", "name", "kind", "availability", "availability_reason",
              "reach", "walk_seconds", "observed", "here"}],          # 附近的地方
  "nearby_objects": [{"object_id", "label", "kind", "in_hand", "slot_id", "takeable"}],
  "opportunities": [{"target_ref", "verb", "label", "description", "duration_seconds",
                     "duration_source", "availability", "availability_reason", "available",
                     "place_availability", "preparation", "preparation_facts",
                     "object_id", "object_label", "slot_id", "destination", "location_id",
                     "place_name", "fact_key", "expected_value", "owner_blocked_reason",
                     "observed", "observed_now", "valid_until", "action_grant": False}],
  "unknowns": ["<short code>", ...],
  "unknown_details": [{"code", "detail", ...}],
  "action_grant": False, "schedule_source": ..., "limit": n,
  "truncated": bool, "total_opportunities": n, "order_basis": "kind_group_presentation",
}
```

- 只读本轮**已发生的那次观测**（`last_observation` / `last_available_actions` /
  `last_catalog` / `last_surfaces` / `last_opportunity_view`），不二次观测、不读文件/数据库/目录。
- 名字、路程、营业都来自 World 自己的目录/包（`name`/`indoor_areas`/`reachability`/`availability`），
  读不到就是 `None` + `unknowns`，不美化 id、不补一个"大概开着"。
- `nearby` 是**地方**（A 的 `life_live_context_view._bound_nearby` 就按地方读：`place_id`+`name`），
  `nearby_objects` 才是"手上/伸手可及"的物件；两者不合并。
- `unknowns` 是短码列表（`places_not_observed`、`object_locations_not_exposed`、
  `surface_destinations_not_exposed`、`container_destinations_not_available`、
  `place_opening_state_unknown`、`place_facts_unavailable`、`hands_not_usable_in_this_pose`、
  `area_not_reported`），带细节的一份在 `unknown_details`。
- 没有任何打分/排序偏好/自动选择：`opportunities` 只按**展示分组**（手边动作 → 待着 →
  用掉 → 去哪里 → 姿势）排，组内保持观测自身顺序，`order_basis` 明说这不是推荐。

### 1.3 `WorldBridgeResolver`（同一个类，新增两 verb + 一个读取口）

- `SUPPORTED_VERBS = ("MOVE","CONSUME","POSE","REST","PICK_UP","PLACE")`；`resolver_params(verb, target)`
  对两 verb 走 `parse_daily_action_target`（解不出就是**提交前**确定拒绝
  `code="DAILY_ACTION_TARGET_INVALID"`, `transport_result=DEFINITE_PRE_SUBMIT`）。
- `resolve_action(idempotency_key=..., verb=..., target=..., expected_dependencies=...)` 之后：
  `status="committed"` + `details = {observation_source, post_state_only: True,
  post_state_proves_this_execution: False, daily_placement, placement_fact}`。
- 成功后 A 可查真实 placement：`resolver.read_daily_placement(object_id) ->
  {observed, object_id, reason, hands, placement, world_revision, location}`（只读一次 `get_snapshot`，
  读不到就 `observed: False` + reason，绝不编一个位置）。
- 已有四 verb（MOVE/CONSUME/POSE/REST）行为未变；幂等仍按**原 key** 缓存，UNKNOWN 仍按同一
  `execution_id` 对账（`execution_id_for` 未改）。

## 2. owned delta（当前行号）

| 文件 | 改动 |
|---|---|
| `components/life/scripts/life_daily_action_targets.py`（新，606 行） | `DAILY_ACTION_REF_PREFIX`(82)、`HAND_SLOTS`(94)、`PLACEMENT_ROOM/SURFACE`(97-99)、`MAX_FIELD`(108)、`FACT_KEY_DAILY_PLACEMENT`(114)、`EFFECT_*`(132-133)、`encode_daily_action_target`(190)、`parse_daily_action_target`(250)、`daily_action_effect_predicates`(339)、`build_daily_action_step`(345)、`daily_action_completion`(414)、`daily_action_observed_fact`(458) |
| `components/life/scripts/world_move_opportunity_source.py` | `PICK_UP_VERB`/`PLACE_VERB`(89-90)、`DAILY_ACTION_REF_PREFIX`(135)、`HAND_MANIPULATION_SECONDS`/`POSTURE_SECONDS`/`DURATION_SOURCE_*`(141-147)、`observed_daily_action_opportunities`(374)、`_held_objects`(482)、`_daily_destinations`(496)、`opportunity_view` 增 dailyact 分支（fact_key/expected_value/destination/slot_id）、`observe` 收 daily + `last_observed_at`/`last_surfaces`/`last_destination_reason`(1441)、`_surfaces_here`(1589)、`daily_life_context`(1613)、上下文助手 `_object_labels`/`_place_names`/`_place_facts`/`_area_name`/`_body_context`/`_takeable_here`/`_nearby_objects`/`_context_order_key`/`_duration_seconds`/`_hand_label`/`_preparation_text`/`_nearby_places`/`_preparation`/`_context_opportunity`(890-1288)/`_context_unknowns`(1289) |
| `components/life/scripts/world_action_bridge_adapter.py` | `SUPPORTED_VERBS`(69)、`DAILY_ACTION_VERBS`(73)、`resolver_params` 增 daily 分支(81)、`resolve_action` 增解码闸(≈300-315)、`_success` 增 daily details(393)、`read_daily_placement`(474)、`_fresh_snapshot`(494)、`_placement_view`(507)、`_daily_placement_details`(531)、`RealWorldActionAdapter.EXTRA_VERBS`(625) + `validate_prepare` 只对两 verb 放宽(627) |
| `tests/test_world_daily_hands.py`（新） | 6 个纯编码用例 + 6 个真实 owner 用例（见 §4） |
| `docs/engineering/v04-lived-day-world-result.md`（本文件）、`.agents/notes/implemented/architecture/2026-11-13-v04-lived-day-world-daily-actions.md`（新） | 回执与决策笔记 |

## 3. 观测到 owner 的事实（不是猜的）

- `get_available_actions` 的 `PICK_UP` 条目本来就给 `{object_id, slots:[空闲手槽]}` 候选（当前地点、
  `takeable=true`、姿势可动手、手有空位才会 `available: true`）；本包只是把同一份候选翻译成
  `dailyact:pick_up:...`，**没有**另建可拿物目录。
- `PLACE` 条目只给 `available = bool(held)`，没有目的地候选；目的地由两处真实来源组成：
  ① 快照自己的 `location.place_id/area_id`（= owner 的 destination 规则要比较的那一对）；
  ② `get_environment`（只读 gateway 命令）里 `surfaces_here` 投影出的**本地点**台面。
  读不到台面 → 只给"这间房"，并在 `unknowns` 里写 `surface_destinations_not_exposed`。
- `container` 目的地被 owner V1 明确拒绝（`DESTINATION_NOT_ALLOWED`），所以编码层也不接受。
- PICK_UP/PLACE 的动作时长用 owner 自己的 manipulation 负载（`body_consequence` 的 2.0s），
  姿势用 `MOVEMENT_SECONDS_BY_ACTION["POSE"]`（5.0s）；这是**副本**，注释里写明来源。

## 4. 验证（命令 / 退出码）

WSL（`/root/nya-venv/bin/python`，Python 3.12.3），`PYTHONDONTWRITEBYTECODE=1`：

| 命令 | 结果 |
|---|---|
| `python -m unittest tests.test_world_daily_hands -v` | **12/12 OK**（exit 0，约 41s）：6 纯编码 + 6 真实 owner |
| `python -m unittest tests.test_world_daily_hands tests.test_day_life_body_actions` | **26/26 OK**（exit 0，约 34s） |
| `python -m unittest tests.test_world_daily_life_consume` | **6/6 OK**（exit 0，约 39s；一次运行曾出现 `ALREADY_DONE != ACTED`，同命令重跑 OK，与本包无关的 closeout 抖动） |
| `python -m unittest tests.test_world_daily_life_continuous` | **1 FAIL**（exit 1）：`test_a_short_continuous_episode_runs_through_the_real_owners` 期望 `ACTED` 得 `ALREADY_DONE` |

退出码口径：Windows 原生无法运行本模块（`test_one_day_life`/`action_reality_ledger` 在导入期
`import fcntl`），所以上述全部在 WSL 里跑；每条命令的退出码都在同一行读取过，OK 的均为 0。

`test_world_daily_life_continuous` 的红灯**不是本包引入**：把本包三个源码文件 + 新测试用
`git stash push -u -- <这 4 个路径>` 摘掉后，同一命令仍然在**同一个断言**、同一条
`ABANDON_NONTERMINAL_CHOSEN` / `AR0_READING_CLOSEOUT_WORLD_ROUTED` 路径上失败（随后 `git stash pop`
原样恢复，四个文件 SHA 与摘掉前一致）。它失败的那一轮里 `world_opportunities` 完全没有 dailyact 目标。

本包用例覆盖的那条链（`tests/test_world_daily_hands.py`）：

1. 真拿起：owner 候选 → `dailyact:pick_up:left_hand:i-cup-01` → `WorldBridgeResolver` → Intent
   Executor → `gateway.sock` → Resolver → **substrate 里 `placement.kind == "hand"`、快照 `hands.left_hand == i-cup-01`**；
   `details.placement_fact.satisfied is True`；`post_state_proves_this_execution is False`；
   同 key 重试复用同一 `execution_id` 且 substrate revision 不变。
2. 真放下：拿着之后才出现 PLACE，目的地是 `{kind: room, home, bedroom}` → 提交后
   `placement.kind == "room"` 且位置/区域回到 home/bedroom、两手都空；旧的"拿起"事实在新状态下
   `satisfied False`（后置状态不证明归属）。
3. 拒绝不伪装：没拿着就 PLACE → `rejected` 且 `transport_result != OK`；未知物件 → `rejected`；
   畸形 target → `code="DAILY_ACTION_TARGET_INVALID"` + `DEFINITE_PRE_SUBMIT`，substrate revision 未动。
4. 现场语义：`location.name`/`area_name` 等于目录里的名字；`body.hands` 空 + `hands_known`；
   `nearby` 第一项是 home（带目录的 `walk_seconds`）；`nearby_objects` 只有 CUP，放在 park 的
   FRAME 不在里面；`unknowns` 含 `container_destinations_not_available` /
   `object_locations_not_exposed` / `places_not_observed`；`limit=1` 时 `truncated` 为真。
5. consumer 兼容：A 的 `life_live_context_view.read_live_context(provider, limit=4)` 直接吃到
   `source == provider.daily_life_context`、`status == available`、目标/label/地方都在。
6. owner 侧事实一变，候选就消失（拿起来之后 PICK_UP 不在、PLACE 才在）。

## 5. 限制与 A 侧需要接的两处（本包没碰，属 A 的写集）

1. `action_reality_ledger.WorldActionAdapter.validate_prepare`（`action_reality_ledger.py:1766`）的
   verb 集合仍是 `{"MOVE","POSE","CONSUME","REST"}` —— 生产 AR-0 用的是它（或其子类
   `RealJourneyWorldActionAdapter`），**PICK_UP/PLACE 会被它自己的合同层拒掉**。
   本包的 `RealWorldActionAdapter.validate_prepare` 只对自己那个适配器放宽（同一 payload 形状，
   多两个 verb 的准入）。A 若走 `RealJourneyWorldActionAdapter`，需要在 AR-0 的 verb 集合里加这两项。
2. `world_journey_action_adapter.DELEGATED_VERBS`（`world_journey_action_adapter.py:73`）目前是
   `("CONSUME", POSE, REST)`；生产旅程 resolver 若不把 PICK_UP/PLACE 列进去，会当作
   `UNSUPPORTED_VERB` 拒掉。加这两项的语义与本包一致（瞬时 owner 变更，走正式授权链）。
   两处都可以直接用 `world_action_bridge_adapter.DAILY_ACTION_VERBS`（= `life_daily_action_targets.DAILY_ACTION_VERBS`）。
3. 目的地目前只有"当前房间 + owner 投影出的本地点台面"。没有家具（`world_property_economy` 的 OWNED
   实例）时 `surfaces` 为空，这是真实情况而不是缺省：此时 `unknowns` 明说只有房间可选。
4. `daily_life_context` 的 `opportunities` 里 `availability: "AVAILABLE"` 说的是**verb** 被 owner 允许；
   具体目的地是否会被 Resolver 拒，仍由提交时 owner 判定（`place_availability` 是地点营业事实，不是授权）。
5. 未做（明确不在本包内）：把 dailyact 接进 A 的 AG-1/AG-2/AR-0 路由、prompt 裁剪、完成事实写入
   LR-5；那是 A 的活。本包只保证"接口是这个形状、事实是这个来源"。
