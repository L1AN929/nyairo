# v0.8 通用物件动作积木、动态贴纸与顺手小套路

日期：2026-11-16 · 工单：`v08_open_living_traces` · 零产品 LLM 调用（`llm_calls == 0`）

## 1. 状态与接口

状态文件：`<root>/phone/v08_living_traces.json`，`schema_version = world.v08.open_affordance.v1`。
读函数 100% 零写（不建目录、不建文件、不改 mtime）；写函数只做同目录临时文件 + `os.replace` 的原子替换。

核心引擎：`components/world/scripts/v08_open_affordance.py`

| 能力 | 入口 |
| --- | --- |
| 可达物件发现 | `discover_reachable_objects(root)` |
| 六个通用动作 | `execute_universal_verb(root, payload, actor_side="subject")` |
| 主体贴纸 | `apply_living_trace(...)` / `clear_living_trace(...)` |
| 伴侣贴纸 | `leave_partner_trace(...)` / `clear_partner_trace(...)` |
| 自然淡去 | `evaporate_expired_living_traces(root, now_iso)` |
| 小套路 | `save_personal_routine` / `run_personal_routine` / `retire_personal_routine` |
| 只读投影 | `read_living_traces_view(root, viewer="subject"|"partner")` |

HTTP：`ROUTE_V08_LIVING_TRACES = /api/phone/v08/living-traces`（GET/POST），
封装在 `components/phone/v08_living_traces_view.py`，由 `service.py` / `server.py` 挂载。

## 2. 六个通用动词

`RELOCATE` / `INSERT_EXTRACT` / `INSCRIBE` / `TOGGLE_ADJUST` / `USE_CONSUME` / `COMBINE_SPLIT`。
每个动词声明需要的特征（`movable` / `insertable` / `inscribable` / `switchable` /
`usable` / `thermal_or_damp` / `combinable` / `container`），物件没有该特征即
`409 TRAIT_MISMATCH`。不存在的物件是 `404 OBJECT_NOT_FOUND`；`INSERT_EXTRACT`
禁止自包含；`COMBINE_SPLIT` 保持双方身份，不新建物件、不改钱包。生病状态
（`phone/v03_ecology.json` 明确 `blocks_strenuous_outing`）且动作是户外重负荷时返回
`409 ILLNESS_GATE_BLOCKED`。

物件来源：七个固定居家锚点（窗、书桌、床、门牌、置物架、廊下晾架、盆栽），加上已落盘的
真实 `stance_containment.json`（容器与小物）、`workbench_tray.json`（相片/素材/未完稿）、
`world_property_economy.json`（已入手小物）。文件不存在就不增补，不猜、不报错。

## 3. 动态贴纸（Living Traces）

* `fade_mode="timed"`：`ttl_minutes` 5–1440（默认 60），到点由
  `v03_clock_evaporation.advance_projection` 静默剪除，无墓碑、无反思队列。
* `fade_mode="until_touched"`：主体对该物件做任一通用动作、或主体贴新贴纸时真正碰到，
  即标记淡去；伴侣自己的新贴纸只清理伴侣自己的 `until_touched` 旧痕。
* 上限：活跃 24 条、伴侣 8 条、同一 `(object_id, author_side)` 2 条；超限平缓替换最旧一条。
* 伴侣贴纸强制 `visibility="shared"`；私密贴纸/铭文/套路在伴侣投影中被彻底剥离。
* 撤回：`clear_partner_trace` 只能撤伴侣自己的（主体的是 `409 AUTHOR_MISMATCH`），
  不存在的贴纸是 `404 TRACE_NOT_FOUND`。

## 4. 自攒顺手小套路

2–4 步，最多 8 套，`when_cue` 为 `rainy|evening|morning|chilly|focused|any`。
保存时先校验每一步引用的物件与特征。运行时逐步真实执行，某一步物件不可达或健康门控
拒绝就**干净停在真实断点**，返回已执行步骤与阻塞原因，不回滚合法前序步，也不虚报完成。
退役是平和删除，无连续打卡、无欠债、无愧疚文案。

## 5. 日常循环与上下文

* `v02_v03_daily_loop_bridge.collect_v02_v03_loop_candidates` 每轮最多贡献 **2** 条候选：
  一条 `v08_routine:<id>`（cue 真实匹配），一条 `v08_open_act:<object_id>:<mode>`
  （伴侣刚留痕时优先把该物件放进来，让主体自己决定要不要回应）。
* `v02_v03_round_event_for_selected_candidate` 产出 `RUN_PERSONAL_ROUTINE` /
  `OPEN_LIVING_TRACE` 事件，`_V02_DAILY_SETTLE_HANDLERS` 调引擎真实结算，`operation_key`
  同 `decision_id` 幂等。
* Cycle / Resident 走既有通用 descriptor/event 通道，无需前缀特判。
* `lived_context_bridge` 以零写只读方式最多注入约 150 字的屋里动态贴纸与套路提示，
  总长仍受 `MAX_LIVED_CONTEXT_CHARS = 1800` 约束；私密条目带
  `[未向伴侣公开·勿顺嘴说漏]` 或根本不读取。

## 6. 权限与隐私

* GET 复用 `_guard_common` / `_desk_subject_view`：无凭据为伴侣视角，错误或过期凭据 403，
  查询串 400 `UNKNOWN_QUERY`，全程零写。
* POST 复用 `_guard_mutation`：缺 CSRF 403；伴侣动作 `leave_partner_trace` /
  `clear_partner_trace` 只需 CSRF；其余主体动作缺有效 `X-Phone-Capability` 即
  `403 FORBIDDEN_ACTOR_SIDE`。
* 夹带 `uid` / `author_side` / `by` / `actor` / `subject` 一律
  `409 CLIENT_IDENTITY_REFUSED`；字段白名单外返回 400 `INVALID_FIELDS`。

## 7. 本次验证

WSL `/tmp/rc104-venv` 全绿：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest \
  tests.test_v08_open_living_traces \
  tests.test_v02_phone_http_surfaces \
  tests.test_v02_hermes_and_bridge_wiring \
  tests.test_hermes_phone_brain_wiring \
  tests.test_hermes_hot_reconnect_and_surfaces -v
# Ran 68 tests ... OK
```

三份冻结锚点（`autonomous_life_assembly.py` / `life_sharing_service.py` /
`readonly_recall_adapter.py`）未改动一字节。

## 8. 世界包与编辑器直通说明

WorldGen 生成的 4 套风貌小镇与世界编辑器新画/新改的物件，只要真实落在实例目录里，
就能被 `discover_reachable_objects` 直接发现，不需要再写后端代码。全部读取仍是零写：
不建目录、不建文件、不改 mtime。

* **当前位置**：按只读顺序尝试 `world/world_runtime.json`、`world/runtime_state.json`、
  `world/state.json`、`data/world/world_state.json`、`phone/desk_presence.json` 的
  `current_location_id` / `location.place_id` / `current_location` 字段；都读不到时
  保守视为 `home`。`home` 及其子区域（`home/bedroom`、`bedroom`、`study` 等）算在家。
* **WorldGen 包**：`world_package/objects.json`、`world/objects.json`。按
  `location_id` 与当前位置匹配（含双向前缀与同属 home 的识别），带 `affordance_traits`
  时只保留合法标准标签并保序采用；未标注时由
  `_infer_traits_from_world_object` 依据 `kind` / 中文名称 / `portable` / `capacity` /
  `actions` 确定性推断，至少给出 `usable`。来源记为 `world_package`。
* **编辑器 v3**：`world/nyairo-world.json`、`world/edited_world.json`、
  `phone/edited_world.json`、`world/scene.json`（识别 `nyairo.world-package.v3` 或同时
  带 `rooms[]` + `objects[]` 的文档）。按 `room_id` 匹配当前区域；在家时匹配 home 区域，
  单房间自定义世界视为该住户的家。`room.name` 作为 `location_hint`，来源记为
  `world_editor`。
* **可达集合**：在家时七个固定居家锚点与原先 100% 一致；明确身处 `cafe` / `park` 等
  外出地点时，居家锚点让位给当地世界包物件与真正随身的物品，不再把家里的床和晾衣架
  当作眼前可上手的东西。同名实时物件按来源加前缀防冲突，原 `object_id` 不丢。
* **`focused` 时机**：`context_cues_zero_write` 只有在真实存在未清工作台托盘
  （`phone/workbench_tray.json` 的 `work` / `images` / `materials`）、活跃阅读工作台
  （`phone|life/reading_workbench.json`）或明确活跃的专注状态
  （`state/v02_agency_depth_state.json` 等）时才加入 `focused`；文件缺失或格式损坏就
  安静地不匹配。`match_routines_for_context` 是给日常推荐调用的只读入口，调用方也可
  直接传入已解析的 `cues`。仓库内 `v02_v03_daily_loop_bridge._v08_context_cues_zero_write`
  仍是现有日常链的独立实现，本工单未改动 `components/life/`；把它切到该入口需要另开
  工单（当前写集明确排除 life 组件）。

