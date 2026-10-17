# 短 runbook：街区连续生活（L 包）怎么跑、怎么看

> 本包不新增入口，也不新增 owner。它只在既有 `autonomous_life_cycle` 里补两条事实边界（提前结束
> 停留、关店不许再造在场），并把"多地点连续生活"用真实本地 owner 跑成一段可数的 episode。

## 1. 定向验证（本包自己的证据）

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=12345 TMPDIR=/tmp/nyaw05r.jdK3q/nb-life-r1
bash $S/neighborhood-life/nb_run.sh test_world_neighborhood_life test_life_stay_early_exit
# → Ran 1 … OK / MODULE_EXIT 0（本地两次实测 48.3s / 68.1s）
# → Ran 2 … OK / MODULE_EXIT 0（本地两次实测 32.7s / 39.3s）
```

`nb_runner.py` 只把 `shutil.rmtree` 换成 no-op（夹具保留），断言一字未改；被测文件里没有
`TemporaryDirectory`/`unlink`/`rmtree`/socket unlink。`TMPDIR` 指到 `nb-life-r1/`，所以 home 落在
`/tmp/nyaw05r.jdK3q/nb-life-r1/w05inst-*` 并保留。

## 2. 受影响的旧 exact ID（只跑这两个，不跑整套）

```bash
bash $S/neighborhood-life/nb_run.sh test_world_daily_life_continuous test_world_daily_life_plan_change
# → Ran 1 … OK / MODULE_EXIT 0（~37s / ~26s）
```

这两支是停留语义的直接邻居：旧的"到点完成"与"闭店改主意"行为必须一字未变。

## 3. 怎么读一段 episode

一轮结果里与本包相关的字段：

- `outcome`：`ACTED` / `IN_TRANSIT`（出发≠到达）/ `NO_ACTION`（合法地什么都不做）/ `ABANDONED`
  （提前结束停留）/ `STEP_REFUSED`（被拒，含"关店"）/ `STEP_UNKNOWN`（等对账）/ `NO_MATERIAL`。
- `step_path`：`STAY_CHECKPOINT`（停留的内部 checkpoint，没有 AR-0 Action）/
  `STAY_ENDED_BY_CHOICE`（停留被 Decision 提前结束）。
- `stay`：
  - 自然完成：`phase=END`、`elapsed_seconds>=planned_seconds`、`progress_state=COMPLETION_ELIGIBLE`，
    `canonical_state.foreground_activity_ref` 变 `null`；
  - 提前结束：`phase=ENDED_EARLY`、`exit_decision=ABANDON`、`completed:false`、
    `completion_claimed:false`、`remaining_seconds>0`、`owner_status=ABANDONED`、
    `owner_terminal_reason_code=ABANDONED_BY_DECISION`、`owner_ended_at`、
    `owner_last_transition_ref`、`foreground_released:true`、`resumable_as_stay:false`；
  - 被拒：`refused=WORLD_PLACE_CLOSED_AT_STAY_CHECKPOINT`（关店）或
    `WORLD_POSITION_DOES_NOT_MATCH_STAY`（世界把她放在别处）或 `STAY_ALREADY_STARTED`（不许重置
    起算点），此时 Activity 仍 `ACTIVE`、前台不释放。
- `journey`：`destination_location_id` / `segment_status` / `expected_arrival_at` /
  `remaining_seconds`。**注意**：owner 的 `expected_arrival_at` 用它自己的墙钟，`observed_at` 是
  本段唯一的注入时刻——两个不要相减（那就是双时钟造假）。
- `world_opportunity_view`：`kind`（`MOVE`/`CONSUME`/`STAY`）、`target_ref`、`availability`
  （`OPEN`/`CLOSED`/`UNKNOWN`）、`walk_seconds`，全部 `action_grant:false`（事实，不是授权）。
- 拒绝的退出：`outcome=DECISION_FAILED`（AG-1 判不合法，`detail` 里是
  `INVALID_MODEL_OUTPUT:DECISION_CANDIDATE_INCOMPATIBLE`）或 `outcome=ADOPTION_REJECTED`
  （adoption 层拒绝），两种都不写 `stay` 事实、不动前台。

owner 侧回读：`runtime.activity_read.get_activity(<activity>)` 看 `status` / `ended_at` /
`terminal_reason_code` / `last_transition_ref`；`runtime.progress_read.list_checkpoint_updates(<activity>)`
看 LR-5 记了几个 checkpoint（提前结束的停留**不会**有 `checkpoint:stay:` 的到点 checkpoint）；
`memory.port.recent()` / `memory.port.prompt_view()` 看本次 World 后果有没有进 M0。

## 4. 真实实例里怎么让它发生

1. 用随仓的有限街区包：

```bash
python -m components.worldgen instance --package examples/worlds/daily_neighborhood_v1 \
  --home <隔离 world home>
```

   `one_day_life.prepare(..., world_package=<repo>/examples/worlds/daily_neighborhood_v1)` 直接把它交给
   `init_instance`，初始位置 `home/bedroom`。单跳图：`home-front 90s`、`front-corner 120s`、
   `front-reading 150s`、`corner-park 180s`、`corner-cafe 120s`。营业：`reading_point 10–20`、
   `cafe 工作日 8–18`、`park 6–22`、`home/street_front/street_corner` 常开。

2. 装配（生产装配 + 真实 AR-0 适配器 + 观察缝）：

```python
runtime = build_production_life_runtime(..., world_action_adapter=RealJourneyWorldActionAdapter(
    JourneyWorldResolver(environment=env)))
cycle = AutonomousLifeCycle(runtime, world_opportunity_provider=WorldMoveOpportunityProvider(
    client=WorldBodyClient(environment=env), action_intent=action_intent,
    environment=env, world_home=world_home))
cycle.evaluate_once(trigger_ref="evt:daily", act=True)
```

   环境需要 `WORLD_BODY_INTENT_MUTATION_GATE=canonical` 与实例自己的 `WORLD_BODY_SOCKET`。

3. 一次出门 = 一轮一次单跳：`IN_TRANSIT` 之后用 `cycle.settle_world_journey(observed_at=<owner 公布的
   due>)` 结算到达；`STEP_UNKNOWN` 用 `cycle.reconcile_unknown_step(action_id=…, occurred_at=…)`
   对同一次执行（不重发 START、不 sleep）。

4. 停留：`stay:<place>:<affordance>:<seconds>` 只在她真的所在、且 owner 说 `OPEN` 的场合出现；
   到点那一轮才写完成值。想让她**提前**离开，就让她在那一轮给出 `ABANDON`（AG-1 在当前 Activity 上
   允许它）——不要改 elapsed，也不要手写完成事实。

## 5. 回滚

本包全是代码、测试与文档，未改任何 owner 数据文件；回滚就是放弃这批 UNCOMMITTED 改动
（`git status` 可见清单）。没有 stage/commit/push，也没有删除任何夹具。
