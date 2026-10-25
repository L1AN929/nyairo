# 世界环境事件目录（2026-10-25）

本目录是**默认汐见市**的有效模板清单，共 24 条：日常 8、会变 8、有限跨天 8。
它说明每条事件的来源、时间窗、地点、是否跨天以及她不管时会怎样；不是每轮
广播量。每日投影仍受 `MAX_EVENTS_PER_DAY = 3` 约束，有限跨天阶段优先占位，
其余名额给单日模板轮转。

## 生产入口与字段

| 环节 | 位置 |
| --- | --- |
| 模板常量 | `components/world/scripts/world_ambient_events.py::_TEMPLATES` |
| 活动世界汇总 | `world_runtime_data.WorldData.ambient_templates` |
| 世界包模板 | `examples/worlds/<id>/ambient.json`（v1 五字段 ≤16；v2 显式 kind ≤24） |
| 正式事件观察 | `materialize_ambient_events` / `active_ambient_events` |
| World 读口 | `world_living_skeleton.observe_world`（`CHIYO_WORLD_AMBIENT_EVENTS_ENABLED` 开启时） |
| 目录视图 | `ambient_template_catalog()`（测试/工程用，不是事件实例） |

事件字段保持既有九个：`event_id / world_id / local_date / location_id /
event_type / payload / valid_from / valid_until / provenance`。`payload` 只有
`summary`。模板的类别与有限跨天定义只是目录元信息，不进入事件返回结构。

有限跨天身份：`event_id = ambient-finite-<作者给定 identity>`。同一事件在
`starts_on..ends_on` 内按天投影阶段；期限结束后不再生成，+8/+16 天或远未来
都不会复活，也不会滚动生成新身份。日期是作者给定的世界日期，不按运行日平移。

## 默认汐见市 24 条

| # | event_type | 类别 | 地点 | 窗口 | 跨天/周期 | 她不管时 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | school_notice | 日常 | school | 00–24 | 否 | 当天结束消失 |
| 2 | home_window_sound | 日常 | home | 00–24 | 否 | 当天结束消失 |
| 3 | convenience_store_morning_delivery | 日常 | convenience_store | 06–10 | 否 | 时段结束消失 |
| 4 | school_gate_sweeping | 日常 | school | 07–09 | 否 | 时段结束消失 |
| 5 | home_goods_store_display | 日常 | home_goods_store | 09–19 | 否 | 时段结束消失 |
| 6 | park_free_paper | 日常 | park | 07–19 | 否 | 时段结束消失 |
| 7 | nearby_street_school_children | 日常 | nearby_street | 15–17 | 否 | 时段结束消失 |
| 8 | home_morning_light | 日常 | home | 06–09 | 否 | 时段结束消失 |
| 9 | park_maintenance | 会变 | park | 08–18 | 否 | 时段结束消失 |
| 10 | convenience_store_promotion | 会变 | convenience_store | 18–22 | 否 | 时段结束消失 |
| 11 | street_construction | 会变 | nearby_street | 06–20 | 否 | 时段结束消失 |
| 12 | school_afternoon_practice | 会变 | school | 15–18 | 否 | 时段结束消失 |
| 13 | convenience_store_evening_queue | 会变 | convenience_store | 17–20 | 否 | 时段结束消失 |
| 14 | park_sunset_shadows | 会变 | park | 16–19 | 否 | 时段结束消失 |
| 15 | home_goods_store_closing | 会变 | home_goods_store | 17–19 | 否 | 时段结束消失 |
| 16 | nearby_street_evening_lights | 会变 | nearby_street | 18–22 | 否 | 时段结束消失 |
| 17 | street_drainage_notice | 有限跨天 | nearby_street | 10-24 07–24；10-25 06–18 | identity `street_drainage_notice_20261024` | 施工结束，围挡撤除 |
| 18 | park_lawn_rest | 有限跨天 | park | 10-25 08–20；10-26 08–18 | identity `park_lawn_rest_20261025` | 轮休养护期满，围栏撤走 |
| 19 | school_culture_festival_prep | 有限跨天 | school | 10-26 09–17；10-27 09–16 | identity `school_culture_festival_prep_20261026` | 筹备布置结束，场地恢复上课 |
| 20 | store_sidewalk_repair | 有限跨天 | convenience_store | 10-27 08–20；10-28 07–17 | identity `store_sidewalk_repair_20261027` | 人行道维修结束，窄道撤除 |
| 21 | home_goods_delivery_arrival | 有限跨天 | home_goods_store | 10-28 10–19；10-29 10–18 | identity `home_goods_delivery_arrival_20261028` | 新货上架完毕，纸箱清走 |
| 22 | home_water_notice | 有限跨天 | home | 10-29 12–23；10-30 09–15 | identity `home_water_notice_20261029` | 水管检修完成，供水恢复正常 |
| 23 | nearby_street_leaf_cleanup | 有限跨天 | nearby_street | 10-30 08–18；10-31 07–16 | identity `nearby_street_leaf_cleanup_20261030` | 落叶清运完成，街道恢复 |
| 24 | park_event_stage_setup | 有限跨天 | park | 10-31 09–19；11-01 08–20 | identity `park_event_stage_setup_20261031` | 活动结束，棚子拆走 |

以上为**内建汐见市**。每条第 1 天是“预告/开始”，第 2 天是“进行中/随后状态”；
8 条事件错开一天排布。按目录日期，2026-10-24 和 11-01 各只有一条处于阶段中，
10-25～10-31 每天最多两条（本组恰为两条）；实际可见还受地点与各自小时窗口限制，
不能把日期范围等同于全天可见。2026-11-02 起内建默认世界不再有跨天事件；
这是作者给定的内容日期，不是通过运行日滚动保证“总有人事”。

## 街区包 daily_neighborhood_v1 的 8 条有限事件

[`examples/worlds/daily_neighborhood_v1/ambient.json`](../../examples/worlds/daily_neighborhood_v1/ambient.json)
是 `worldgen.ambient.v2`，共 24 条 = **16 条 `single_day` + 8 条 `finite`**。
包目录分类为 `daily 16 / changing 0 / cross_day 8`，与内建汐见市的 8/8/8 不同；
包内单日模板没有“会变”类别承诺，是否随时间变化只由各自的
`start_hour`/`end_hour` 决定。下表是包内实际 8 条有限事件，**不是**内建的
school、home_goods 等条目，两者的 identity 与日期各自独立。

| # | identity | event_type | 地点 | 起止 | 阶段 1（日／时窗） | 阶段 2（日／时窗） | 忽略后 end_reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `street_front_drainage_20261024` | `finite_street_drainage` | street_front | 10-24 → 10-25 | 10-24 07–21 | 10-25 07–18 | 施工结束，围挡撤除 |
| 2 | `park_lawn_rest_20261025` | `finite_park_lawn_rest` | park | 10-25 → 10-26 | 10-25 08–20 | 10-26 08–18 | 轮休养护期满，围栏撤走 |
| 3 | `cafe_sidewalk_20261026` | `finite_cafe_sidewalk` | cafe | 10-26 → 10-27 | 10-26 09–19 | 10-27 08–17 | 人行道翻修结束，桌椅摆回 |
| 4 | `reading_point_cart_20261027` | `finite_reading_cart` | reading_point | 10-27 → 10-28 | 10-27 10–18 | 10-28 09–17 | 旧书整理完毕，书车推走 |
| 5 | `street_corner_works_20261028` | `finite_street_corner_works` | street_corner | 10-28 → 10-29 | 10-28 08–20 | 10-29 07–16 | 信号灯检修完成，恢复正常 |
| 6 | `park_stage_20261029` | `finite_park_stage` | park | 10-29 → 10-30 | 10-29 09–19 | 10-30 08–20 | 活动结束，棚子拆走 |
| 7 | `cafe_delivery_20261030` | `finite_cafe_delivery` | cafe | 10-30 → 10-31 | 10-30 10–18 | 10-31 09–17 | 新豆子上架完毕，纸箱清走 |
| 8 | `home_water_20261031` | `finite_home_water` | home | 10-31 → 11-01 | 10-31 12–22 | 11-01 09–15 | 水管检修完成，供水恢复正常 |

包内有限事件同样第 1 天为预告/开始、第 2 天为进行中/随后状态，投影身份为
`ambient-finite-<identity>`。按作者日期，10-24 与 11-01 各只有一条处于阶段中，
10-25～10-31 每天最多两条（本组恰为两条）；实际可见仍受地点与小时窗筛选，
不等于全天可见。11-02 起该包不再有有限事件。上表只描述环境叙述，不表示真的
封路、修好或上架；也不写入价格、库存或 NPC 承诺。

## 会变的来源

内建汐见市 24 条中“会变”8 条全部由既有 World 本地时间窗驱动（`start_hour`/`end_hour`），
**没有**真实动作派生的事实。修路、养护、上架、检修等只作环境叙述，不宣称真的封路、
物品损坏或她修好了；也不改变支付、库存、Body、导航或营业规则。

## 世界包路径（v2）

- `examples/worlds/daily_neighborhood_v1/ambient.json` 是 `worldgen.ambient.v2`：
  16 条 `kind: single_day` + 8 条 `kind: finite`，共 24 条；`MANIFEST.json` 已用
  既有工具重算。
- 有限事件的 `finite` 声明固定 `identity / starts_on / ends_on / end_reason / phases`；
  阶段日期是绝对世界日期。事件投影使用 `ambient-finite-<identity>`。
- v2 文档可用 `timezone`，但必须与 `world.json` 时区一致；缺省沿用包时区。
- 旧 v1 包（五字段、≤16 条）仍按原语义加载；新读端向后兼容 v1。
- 旧读端只认 v1，遇到 v2 明确拒绝，不静默降级；v1 文档携带 `kind`/`finite`/
  `phases` 等跨天字段会被拒绝。该拒绝行为由测试覆盖，未在历史安装版现场验证。
- 其他世界包（inobe、nagiura、onsawa 等）未修改，仍是 v1 单日模板。

## 边界

- 观察只给当前可知事实，不广播全城、不保证未来；事件不授予动作许可。
- 有限事件期限后不再出现，事后 +8/+16 天或远未来都不复活。
- 跨天投影是确定性的环境叙述，不是持久个人事项、不是生活因果、不是模型自然采用，
  也不产生 Life 资格。
- 无真实 short-day、无产品模型调用、无 G1/24h 声称。
