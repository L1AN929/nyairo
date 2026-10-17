# 短 runbook：世界日常（I 包）怎么跑、怎么看

> 本包不新增入口。它把**既有**统一入口（`one_day_life` / `autonomous_life_cycle`）接上
> W 的机会目录与 F 的消费 verb；运行方式、暂停位、预算、单写者语义都没变。

## 1. 定向验证（本包自己的证据）

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp/nyaw05r.jdK3q
$S/w04venv/bin/python $S/i_daily_runner.py tests.test_world_daily_life_consume
# → Ran 6 tests … OK / PY_EXIT 0
```

`i_daily_runner.py` 只把 `shutil.rmtree` 换成 no-op（夹具保留），断言一字未改；被测文件里没有任何
`TemporaryDirectory`/`rmtree`/`unlink` 清理。

## 2. 相关回归（按需，不要全仓）

```bash
$S/w04venv/bin/python $S/i_daily_runner.py tests.test_one_day_life
$S/w04venv/bin/python $S/i_daily_runner.py tests.test_life_v1_integration
cd components/world/service && PYTHONPATH=.:../scripts \
  $S/w04venv/bin/python $S/i_daily_runner.py test_object_action_m15 test_world_move_candidates \
  test_world_object_capability test_world_body_service test_world_body_bridge \
  test_world_action_authorization test_authorization_hardening test_world_action_lifecycle
```

已知既有红灯（与本包无关，见 `world-daily-i-result.md` §7）：`tests.test_world_daily_opportunities`
的计时出行用例（W 的墙钟依赖）、`tests.test_one_day_life_w05r` 的 UTC 跨日预算用例（墙钟依赖）、
`tests.test_life_v1_restart` 的 START 负控（哈希种子依赖，单跑多数种子通过）。

## 3. 真实实例里怎么让它发生

1. 准备实例（真实 owner 命令，包用随仓的有限街区）：

```bash
python -m components.worldgen instance --package examples/worlds/daily_neighborhood_v1 \
  --home <隔离 world home>
```

`one_day_life.prepare(..., world_package=<repo>/examples/worlds/daily_neighborhood_v1)` 会把这个包
直接交给 `init_instance`（不复制），实例初始位置是 `home/bedroom`。

2. 夹具初始化时按 World owner 的格式声明一份可食物件并放置（**只在初始化做，测试中不凭结果改对象**）：

```python
import world_body_substrate as wbs
# state/world_object_capabilities.json 追加 consumables: {"<object_id>": {"consumable": true, "free_to_consume": true}}
# 基座：wbs.place_object(state, "<object_id>", {"kind": "room", "location_id": "home", "area_id": "home"})
```

放在**常开**的地点（如 `home`）可以让夹具不受真实墙钟影响；放在咖啡店这类有时段的地点则必须让
世界服务的 clock 落在营业时段内（服务用真实墙钟，`world_daily_rhythm` 按时区判定）。

3. 装配与运行（生产装配 + 真实 AR-0 适配器）：

```python
from world_journey_action_adapter import JourneyWorldResolver, RealJourneyWorldActionAdapter
runtime = build_production_life_runtime(..., world_action_adapter=RealJourneyWorldActionAdapter(
    JourneyWorldResolver(environment=env)))
cycle = AutonomousLifeCycle(runtime, world_opportunity_provider=WorldMoveOpportunityProvider(
    client=WorldBodyClient(environment=env), action_intent=action_intent,
    environment=env, world_home=world_home), ...)
cycle.evaluate_once(trigger_ref="evt:daily", act=True)
```

环境需要 `WORLD_BODY_INTENT_MUTATION_GATE=canonical`（World bridge 自己的进程开关）与实例自己的
`WORLD_BODY_SOCKET`。

## 4. 读一轮结果

- `outcome`：`ACTED` / `IN_TRANSIT`（出发≠到达）/ `NO_ACTION` / `NO_MATERIAL`（0 调用）/ `PAUSED_BY_OPERATOR` …
- `world_verb`：`MOVE` 或 `CONSUME`；`world_mutation_ref` / `world_evidence_refs` 是 AR-0 回执里的证据。
- 消费轮：`consumption_receipt_present`、`consumed_object_id`、`consumption_execution_id`、
  `consumption_receipt_ref`；`journey` 字段不出现（消费不是到达）。
- `progress_state`：LR-5 自己的判读（消费完成判据被满足时是 `COMPLETION_ELIGIBLE`）；
  `canonical_state.foreground_activity_ref` 变 `null` 表示 Activity 真的结束并释放前台。
- `material.world_opportunity_view`：这一轮她看到的机会（`kind`/`target_ref`/`availability`/
  `availability_reason`/`walk_seconds`），全部 `action_grant:false`——只是事实，不是权限。
- owner 侧回读：`state/world_body_substrate.json` 的 `consumptions`（回执）与 `placements`（instance 是否还在）、
  `data/body/consequences.jsonl`（Body 后果条数）。

## 5. 回滚

本包全是代码与文档，未改任何 owner 数据文件；回滚就是放弃这批 UNCOMMITTED 改动（`git status` 可见 §9 的清单）。
没有 stage/commit/push，也没有删除任何夹具（`/tmp/nyaw05r.jdK3q/i-daily-*` 保留）。

## 6. 连续一天（I-r1）：怎么跑、怎么读

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp/nyaw05r.jdK3q
$S/w04venv/bin/python $S/i_daily_runner.py \
  tests.test_world_daily_life_play tests.test_world_daily_life_consume \
  tests.test_world_daily_life_continuous
# → Ran 11 … OK / PY_EXIT 0（连续演练单独跑约 22s）
```

连续演练（`tests/test_world_daily_life_continuous.py`）在**同一实例**上依次做：
静默 0 调用 → 真作品推进（M0 行 VERIFIED）→ 合法共处打断（私人 Activity 停保存点）→
出门（真出发/真到达，**不消费**那一支仍在原地）→ 门前街重读 owner 事实（店 `CLOSED`、
`affordances=[]`、`observed=not_observed`、单跳候选里没有它；公园 `OPEN`）并改主意 →
**停留 START/ONGOING/END**（提前一轮不许完成）→ 回家（路上暂停：0 调用、不出发、已出发段照常到）
→ 用掉一份可食物件（回执 + Body 一条）→ RESUME 原私人保存点并 CONTINUE 出真新版本 →
历史经 M0 进入下一次输入（`selected_by=CONTINUITY_HISTORY`）→ 弱线索不改对象。

读一轮结果时注意三类新字段：

- `stay`：`phase`（`START`/`ONGOING`/`END`）、`place`、`affordance`、`planned_seconds`、
  `started_at`（来自 LR-5 的第一个 checkpoint，不是本模块的内存）、`elapsed_seconds`、
  `checkpoint_ref`（到点那轮是 `world_fact:stay:<place>:<descriptor>:<seconds>` 的 checkpoint）；
- `step_path=STAY_CHECKPOINT`：这一轮的 step 是 Life 自己的内部 checkpoint，
  **没有 AR-0 Action**（`world_verb` 不出现）；
- `material.world_opportunity_view` 里 `kind=STAY` 的条目带 `affordance`/`stay_seconds`，
  仍然 `action_grant:false`（事实，不是授权）。

时钟（**I-r2 更正**）：演练只用**一个**注入时刻 `Clock.now`——每一轮的 `observed_at`、每一次出行、
以及每一份营业事实的读取（provider 的 catalog 时刻与 Life 时刻是同一个值）都取它，只有本文件显式
`advance()` 才前进。I-r1 曾把"Life 时钟"和"周六中午目录时刻"写成两个时刻，那是错的：对照不能靠
两个互相矛盾的"当下"得到混合 pass。

已知 owner 侧不稳定：World 的计时出行偶尔回答 `UNKNOWN`（I-r1 记为"同一墙钟秒内连续多次出发"，
根因由父级 explorer 在查，本批不改 W/F）。Life 的处理是**同一 action/execution 先对账**
（`cycle.reconcile_unknown_step`）再结算原 Activity，不 sleep、不重发 START；多段长链仍不在本批
稳定范围内。

## 7. I-r2 增量：UNKNOWN 闭环、营业事实读取、World 后果进 M0

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp/nyaw05r.jdK3q
$S/w04venv/bin/python $S/i_daily_runner.py \
  tests.test_world_daily_life_play tests.test_world_daily_life_consume \
  tests.test_world_daily_life_continuous tests.test_world_daily_life_plan_change
# → Ran 12 … OK / PY_EXIT 0（连续 ~26s、对照 ~19s）
```

- `UNKNOWN`：`cycle.reconcile_unknown_step(action_id=…, occurred_at=…)` 依次做
  (1) journey 账本按 owner 的 travel 记录回读（`settle_only=True`，绝不发起新段）、
  (2) AR-0 `reconcile_action`（同一 id、同一 execution）、(3) AG-2
  `settle_and_propagate_action_result`（结算**原来那个** Activity）。返回里
  `reconciled`/`action_status`/`journey`/`settlement` 都是 owner 的原话；`STILL_UNKNOWN` 时
  什么都不声明。
- 营业事实：`cycle.world_observation_facts()` 把 W 目录在**同一时刻**读到的营业状态写进
  AG-1 的 `observed_facts`，键形如 `world_place_<place>_availability_<STATUS>`
  （ASCII/下划线，生产 prompt 的 `observed_fact_keys` 会保留）。它是事实，不是授权，也不产生候选。
- World 后果进 M0：`_record_world_progress` / `_record_world_consume_progress` /
  `_record_world_stay_progress(complete=True)` 都经 AG-2 既有 outbox
  `emit_experience_handoff(world_refs=…)`；Memory 仍由 `chiyo` 准入，Life 不写行。
  读法：`memory.port.recent()` 里看 `action_ref`/`owner_result`（`artifact_ref` 对 World 后果为
  `null`），下一轮 `observed_fact_keys`/`prior_experiences` 里能看到它。
