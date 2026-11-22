# v0.8 通用物质底座与开放多步行动组合器

日期：2026-11-16 · 工单：`v08-universal-matter-engine` · 零产品 LLM 调用（`llm_calls == 0`）

## 1. 一句话

给实例空间里的每一件真实物件同一套物理属性（单一归属、槽位、热态、熟度、干湿、
洁净、新鲜、份数、怕脏、借物、可见性），再用七个开放微原语任意拼一步到六步的行动；
于是「拿画笔插杯子」「把番茄和生乌冬煮成一碗自己命名的面」「吃一半留一半」「冰箱保鲜
而私人抽屉照样变质」这些场景都不需要再写一条专属剧本。

核心引擎：`components/world/scripts/v08_universal_matter.py`

## 2. 状态与零写读侧

状态文件：`<root>/world/v08_matter_space.json`，`schema_version = world.v08.universal_matter.v1`。

- **首次读不落盘**：文件不存在时，读函数在内存里具现默认空间（9 个区域、5 件默认物件），
  不建目录、不建文件、不改 mtime；只有第一次真实写操作才创建该文件。
- **读侧实时推演**：`read_matter_space_view(root, actor_side=..., now_iso=...)` 在深拷贝上
  把冷却/冷藏/干燥/变质推演到当前时刻，磁盘永远不动。
- **写侧原子替换**：同目录临时文件 + `os.replace`，与既有 v0.8 模块同一风格。
- 能力上限：`MAX_RECENT_ACTS = 24`、`MAX_OPERATION_RECEIPTS = 200`，超出按最旧淘汰。

| 能力 | 入口 |
| --- | --- |
| 只读投影 | `read_matter_space_view(root, actor_side="subject"|"partner", now_iso=...)` |
| 多步开放行动 | `compose_open_act(root, payload, actor_side="subject", now_iso=...)` |
| 伴侣引入物件 | `partner_bring_item(root, payload, now_iso=...)` |
| 伴侣挪动共享物 | `partner_move_shared_object(root, payload, now_iso=...)` |
| 伴侣提议新用法 | `partner_propose_object_use(root, payload, now_iso=...)` |
| 主体回应提议 | `respond_proposed_use(root, payload, actor_side="subject", now_iso=...)` |
| 时钟推进物理 | `settle_matter_physics(root, now_iso=...) -> dict[str, int]` |
| 零写机会扫描 | `scan_matter_opportunities_zero_write(root, now_iso=...) -> list[dict]` |

## 3. 默认区域与物件

9 个默认区域（`zone_id` → 标签 / 环境 / 水来源 / 槽位 / 可见性）：

`home_desk` 书桌台面 · `home_staging_corner` 顺手暂放角 · `home_fridge` 共享冰箱（cold）·
`home_fridge_door` 冰箱门磁贴墙 · `kitchen_counter` 备餐台 · `kitchen_stove` 灶台热源（heating）·
`dining_table` 餐桌台面 · `kitchen_sink` 厨房水槽（water_source）· `subject_private_drawer`
私人小抽屉（private）。

5 件默认物件：`ceramic_cup_01` 白瓷杯（vessel，2 格）、`soup_bowl_01` 深口瓷碗（vessel，2 格）、
`pen_01` 常用勾线笔、`pen_02` 水彩画笔、`sketch_sheet_01` 未收起的参考画纸（`paper_like`）。

**外部物件兼并**：读投影会只读调用 `v08_open_affordance.discover_reachable_objects(root)`，
把不属于已知区域、也尚未物化的自定义物件（WorldGen 包 / world-editor 存档 / 其它真实来源）
附加进列表，默认归入 `home_staging_corner` 并带 `from_open_affordance=True`。当某次真实操作
按该 `object_id` 引用它时，才物化进持久状态，保持统一寻址。既有模块只读调用，不改一行。

## 4. 单一物理归属与槽位

- 每件物件只有一个 `parent_id`；容器的 `children_ids` **完全由所有物件实时 `parent_id`
  派生**，状态里不存在第二份手动计数。
- 严格单层包含：禁止容器套容器、禁止自身含自身（`409 INVALID_CONTAINMENT`）。
- 目标槽位已满（占用 >= `capacity_slots`）再放入 -> `409 CAPACITY_FULL`。
- 移动一个容器时，它的子物件保持 `parent_id == 容器ID`，因此自然随父移动，不会错位。

## 5. 七类开放微原语

| 原语 | 关键校验 | 结果 |
| --- | --- | --- |
| `PLACE_OR_MOVE` | 存在性、自包含、容器嵌套、槽位、表面油污 | 更新 `parent_id` 与 `subject_last_seen_parent` |
| `INTRODUCE_ITEM` | 合法 `item_kind`、目标容量、表面油污；私人目标自动 `private` | 落一件主体引入的小物 |
| `TRANSFORM_CREATE` | 每个源真实存在且未变质、器皿洁净且空 | 任意真原料组合成任意调用者命名的产物 |
| `CONSUME_PORTION` | 变质 / 生食需加热 / 器皿被占 / 份数不足 | 减份；吃空则退场并把宿主容器标 `used` |
| `CLEAN_SURFACE_OR_VESSEL` | 容器内有物 -> `CONTAINER_NOT_EMPTY`；洗碗须在水槽或带 helper | 台面或容器回到 `clean` |
| `DISCARD_ITEM` | 容器内有物 -> `CONTAINER_NOT_EMPTY` | 移除实物，记 `counted_as_intake=False` |
| `OBSERVE_OBJECT` | 存在性 | 对比并刷新“脑内位置”，报告是否被挪过 |

`compose_open_act` 的正载荷：`intent_cn`（1..120）、`steps`（1..6）、可选
`stop_after_step`、`allow_partial`（多步默认 `True`，单步默认 `False`）、`visibility`、
`soils_surface_id`、`strenuous_outing`、`operation_key`。身份键一律 `409 CLIENT_IDENTITY_REFUSED`。

**诚实的中途状态**：任一后步受阻时，前面已真实落地的步骤保留，回执为
`status="partially_applied"` 并带 `failed_step`；`stop_after_step` 只执行到指定步，
`stop_reason="STOP_AFTER_STEP"`。从不谎报整套完成。

**无硬编码菜谱**：`TRANSFORM_CREATE` 不含任何菜名表。真番茄 + 生乌冬放进锅，
由调用者命名 `"无葱番茄乌冬面"`，产物即 `cooked` + `hot` + `fresh`；少了“小葱”就是
真实 `404 OBJECT_NOT_FOUND`，不会凭空造出一根葱。热源判定为 `medium_object_id` 属于
`heating` 区域（或 `kitchen_stove`），热食默认 24 小时保鲜期，默认 2 份。

## 6. 时间与物理演化

每个受时间影响的物件保留 `last_thermal_at` / `last_moisture_at` / `freshness_anchor_at` /
`freshness_settled_at` / `shelf_life_hours` / `effective_exposure_hours`，另有汇总的
`state_clock_iso`。

- **冷藏**：`cold` 区域 20 分钟内把 `room/warm/hot/cool` 转为 `cold`；`frozen` 保持。
- **室温**：`hot/warm` 30 分钟回 `room`；`cold` 45 分钟回 `room`；`wet/damp` 120 分钟转 `dry`。
- **客观变质**：`ratio = 有效暴露小时 / shelf_life_hours`；`>= 1.0` -> `spoiled`，
  `>= 0.75` -> `aging`，否则 `fresh`。`shelf_life_hours=None` 为 `non_perishable`，永不变质。
- **冰箱 6 倍慢速**：`cold` 区域的有效暴露速率是室温的 `1/6`。
- **私人抽屉照样变质**：变质只看客观时间与所在环境，不看有没有人查看。放进去两周，
  即使无人打开，主体视角仍是 `spoiled`。

`settle_matter_physics` 供 60 秒时钟调用；文件不存在时 100% 零写，直接返回全零计数。
存在真实状态且本次确有推进时才落盘。

## 7. 衍生用途：只读派生，不是改名命令

`derived_role_cn` 完全由实时状态合成：

- 容器内有非食材子物 -> `"临时装着<子物名>（盛饮前需先腾空）"`；
- 容器内盛着成品/饮品 -> `"正盛着<子物名>"`；
- 容器脏了 -> `"用过待洗的容器"`；否则 `"空置可用的容器"`；
- 可食用物按新鲜 / 熟度 / 热态 / 份数合成（如 `"热乎刚做好的成品（剩 1 份）"`、
  `"已变质（不可食用）"`、`"等待下锅的生鲜食材"`）；
- 存在伴侣建议时附带 `"；伴侣建议：<用法>（待决定）"` / `"；已采纳用法：…"` / `"；已婉拒用法：…"`。

因此把画笔插进空杯，杯子立刻变成“临时插着画笔的杯子”；此时想盛茶或洗杯都会被
`409 VESSEL_OCCUPIED_OR_SOILED` / `409 CONTAINER_NOT_EMPTY` 拦住；拿走笔并洗干净后，
它自动恢复成“空置可用的容器”。

## 8. 隐私与“非全知”

- 私人抽屉（`visibility="private"`）及其内物件在伴侣投影中**整体消失**：没有区域、
  没有物件，也没有任何 `hidden_count` 之类的计数。
- 伴侣对私人目标的 `partner_bring_item` / `partner_move_shared_object` /
  `partner_propose_object_use` 一律 `404 OBJECT_NOT_FOUND`，不泄漏抽屉存在。
- 伴侣挪动共享物时**故意不改** `subject_last_seen_parent`，只把 `impression_stale` 置真；
  主体必须主动 `OBSERVE_OBJECT` 才会发现位置变化并刷新“脑内位置”。
- 伴侣刚递来 / 放下的物件默认 `subject_last_seen_parent=None`、`impression_stale=True`。

## 9. 身体联动（只读、绝不虚建）

`CONSUME_PORTION` 成功后，如果 `<root>/state/lived_needs_profile.json`（或 `body/` 下同名
profile）**已明确启用且存在**，回执附带温和的 `lived_need_relief_hints`
（饮品 -> `thirst`，食物 -> `hunger`）。文件不存在、未启用或损坏时静默跳过，
**绝不**擅自创建任何 `lived_needs` 状态文件；身体数值仍只由 Body 所有者写入。

## 10. 零写机会扫描

`scan_matter_opportunities_zero_write` 在文件不存在时返回 `[]` 且零写。存在真实状况时，
不靠剧本清单，只按客观物理事实最多合成 2 条开放候选：

1. 餐桌/书桌/备餐台/暂放角有 `used` 容器、满格或 `soiled` 表面 -> `MATTER_USED_OR_CLUTTERED`；
2. 室温下有易腐食材且冰箱有空位 -> `MATTER_PERISHABLE_AT_ROOM`；或手头有生鲜且灶台空
   -> `MATTER_RAW_AND_STOVE_FREE`；或有做好的成品 -> `MATTER_COOKED_FOOD_READY`；
3. 有物件 `impression_stale=True` -> `MATTER_UNSEEN_MOVE`。

候选的 `open_act_payload` 就是可直接交给 `compose_open_act` 的载荷，日常循环桥接只负责
把客观事实变成可选动作，不替用户拍板。

## 11. 错误码

| 状态码 | 原因码 |
| --- | --- |
| 400 | `INVALID_FIELDS` / `INVALID_TARGET` |
| 403 | `FORBIDDEN_ACTOR_SIDE` |
| 404 | `OBJECT_NOT_FOUND` |
| 409 | `CLIENT_IDENTITY_REFUSED` / `CAPACITY_FULL` / `INVALID_CONTAINMENT` / `SURFACE_SOILED` / `VESSEL_OCCUPIED` / `VESSEL_OCCUPIED_OR_SOILED` / `CONTAINER_NOT_EMPTY` / `ITEM_SPOILED` / `ITEM_RAW_NEEDS_HEAT` / `INSUFFICIENT_PORTIONS` / `ILLNESS_GATE_BLOCKED` / `OPERATION_KEY_CONFLICT` |

洗碗的物理约束：容器必须已在 `kitchen_sink`，或调用方传
`helper_object_id="kitchen_sink"`；两者皆无则 `400 INVALID_TARGET`（并在文档中写明这是
“把碗拿到水槽再洗”的显式表达）。擦台面不需要水槽，但**不会**把桌上占位物凭空清除。

## 12. 工程纪律

- 纯标准库 + 只读兼容既有的 `v08_open_affordance`（可达物件发现、病程拦截）；
  未修改任何既有模块。
- `llm_calls == 0`，零网络，零产品大模型调用。
- 文案与角色中性，不含硬编码的人称代词或角色专属名，统一用主体 / 伴侣。
- 校验测试：`tests/test_v08_universal_matter_engine.py`（20 个用例）。

## 13. 自定义世界同步（WorldGen / World-Editor）

- 读侧零写：`read_matter_space_view` 只读已存在的
  `world/package/objects.json`、`world_package/objects.json`、`world/objects.json`，
  以及 `world/editor_package.json`、`world_editor/package.json`、
  `world/world-package.v3.json`；缺失或损坏的 JSON 直接跳过，绝不建目录、建文件或改 mtime。
- 位置过滤沿用 `v08_open_affordance` 的语义：当前位置取自 `current_location_id(root)`，
  默认 `"home"`；WorldGen 物件按 `location_id/room_id` 过滤，World-Editor 还按 `rooms[]`
  匹配当前房间（在家时匹配带“家/小屋”等标记的房间，或单房间世界）。
- 有 `matter_profile` 的物件按 `item_kind / capacity_slots / paper_like /
  shelf_life_hours / portions / ambient_zone / water_source / portable` 归一化后注册：
  - 不可携带（`portable: false`）、有冷/热环境、有水源、或有容量的非手持容器，
    作为额外的自定义 Zone 叠加到读视图（`from_custom_world: true`，共享可见）；
  - 其余作为外部 `matter_object` 叠加，默认落在 `home_staging_corner`，
    保留准确的 `matter_profile` 字段。
- 首次真实写入（`compose_open_act` 的放置/引入/清洗/以自定义灶为热源）才把自定义 Zone
  写入 `state["zones"]`，自定义物件写入 `state["matter_objects"]`；共享自定义 Zone 对
  主体与伴侣都可见，未知目标仍是 404。
- 没有任何自定义世界文件时，默认空间与既有 37 个用例的结果逐字节不变。
- 校验测试：`tests/test_v08_matter_custom_world_sync.py`（4 个用例）。

