# rc1 房间生活施工结果（C：World/Body/经济核心）

契约：[`rc1-room-living-contract.md`](rc1-room-living-contract.md)（冻结给 P/U）。
决策笔记：`.agents/notes/implemented/feature/2026-10-22-rc1-room-living-store-arrange-light.md`。
根：`D:\nyairo-rc1-body-backend`（HEAD `41e818f` = rc1，已验收 phone/worldbody dirty 全部保留）。未 stage/commit/push/tag/package，未改 `MANIFEST.json`，未碰 `components/phone/**`（除 P 自己的文件）、`chiyo_bundle`、`components/phone/web/**`、旧测试。

## 0. 定点返修（主链缺口闭合）

父级只读发现并实测复现：柜台下单只有经济事实，`materialization` 永远是 `pending_arrange`，**substrate 里没有 placement**；`_settle_deliveries` 只调 `economy.realize_due_deliveries` 改经济位置；于是 `arrange_verdict` 拿到 `current = placements.get(item) = None` → `NOT_MATERIALIZED`，真实新购家具到家后**永远不能 ARRANGE**。

复现（修复前实测）：

```
purchase applied PENDING pending_arrange
realize applied 1
economy location home/bedroom
placement None
arrange_verdict False NOT_MATERIALIZED
```

修复（最小、单一 writer）：**到货物化唯一由 owner tick 完成**，复用既有物化实现，不新增第二份。

* `world_phone_shopping.materialize_owned_item()` = 既有 `materialize_purchase()` 的**同一个实现**（中性命名，两处调用：手机渠道购买、owner tick），前置全部来自真实 owner 事实：经济实例存在、`OWNED`、`current_location == "home/bedroom"`、`purchase_voucher()` 由真实 `PURCHASE`/`DEBIT` 证明（不接受任何客户端/P 侧自报能力）。
* `WorldBodyService._settle_deliveries()` 现在两步：`realize_due_deliveries`（temporal 到期）+ `_materialize_delivered()`（所有 **OWNED + 已到货 + 无 placement** 的实例落到 `{"kind":"room","location_id":"home","area_id":"bedroom"}`）。同一 tick 内完成，幂等（下一次 tick 返回 `None`）。
* 语义边界：已有**不同** placement → `PLACEMENT_CONFLICT`，**绝不覆盖**；有消费 receipt → `ALREADY_CONSUMED`，**绝不复活**；不写 hand；`takeable=false` 不变（床仍不可 PICK_UP）。
* 读路径依然一律不物化（`store_shop_projection` / `deliveries_projection` / `home_items_projection` 纯读，测试用 home 指纹断言）。
* 时序因此是：**买 →（temporal 到期 / instant 立即）→ 下一个 owner tick 到货并落位 → ARRANGE 合法**。到货与 tick 之间的 `NOT_MATERIALIZED` 是真实短暂的中间态。

回归（真实链路，**没有任何手工预置 placement**）：`StoreDeliveryToArrangeTests` —— temporal 买→`tick()`（注入时钟 +3h）→ 断言 `realized` + `materialized` + placement + 真 Resolver `ARRANGE` 成功；instant 买→`tick()`→落位；seam 对冲突/未到货拒绝；读不物化。

**同时核实的第二个有证问题（环境 gap 锚点）**：原实现拿 `body.last_advanced_at` 当唯一锚点，旧 root 长时间没推进身体时，开灯后的第一个 tick 就会命中 >900s 的 gap 并**永远拒绝**。现改为 `anchor = max(last_advanced_at, light.since)`（灯 30 秒前才开就只谈这 30 秒），且 gap 分支改为「**不喂任何环境输入**的有界（≤900s）无输入推进」把时钟推到 now，使下一 tick 回到正常窗口；sensory_load 不朝灯档位上升，不补造整夜观察。测试覆盖：`test_turning_the_lamp_on_now_is_not_blocked_by_an_old_body_clock`、`test_a_gap_after_a_stop_applies_no_light_but_unlocks_the_next_tick`、`test_a_long_gap_claims_no_light_but_leaves_a_normal_next_tick`。

## 0.1 定点返修之二：柜台首买 503 `WORLD_LOCATION_UNAVAILABLE`（GET 在店、POST 说没位置）

父级真实 Edge 现场：正常步行到 `home_goods_store`，`GET /api/phone/store` 正常（`available`、`at_store=true`、货架 6 项），紧接 `POST /api/phone/store/purchases` 首买 `simple_desk` 返回 503 `WORLD_LOCATION_UNAVAILABLE`，**没扣钱**；`phone/operations.jsonl` 里那一行是 `{"action":"store_purchase","status":"unavailable","reason_code":"WORLD_LOCATION_UNAVAILABLE"}`。

**根因（只读定位，一处）**：写路径和读路径读位置的**不是同一个函数**。

* `store_shop_projection()`（GET）走 `_read_world_location()`：直读 `data/world/world_state.json` 的 `location.place_id` —— 纯读、无 `world_id` 比较，所以在店就是在店。
* `store_purchase_item()` → `_store_check()` → `_current_world_location()` 走的是 `world_living_skeleton.read_move_state()`：它返回的是**导航节点**（`<place>/<area>`、`<place>/entrance`），并且硬比较 `snapshot["world_id"] != economy.WORLD_ID`（`WORLD_ID` 写死 `"shiomi_city"`）。现场世界是打包世界 `coastal_town_929711` → 比较失败 → 返回 `None` → `WORLD_LOCATION_UNAVAILABLE`。即使 world id 恰好相符，节点串也不等于 `STORE_LOCATION_ID`，还会误判成 `STORE_LOCATION_REQUIRED`。GET 从来不经过这条线，所以两者观测不一致。

**修复（一处实现，结构上不可能再分叉）**：`world_property_economy._current_world_location()` 改为 `place, _area = _read_world_location(hermes_home=…)`；`now` 参数保留在签名里（调用方已在传），但位置判定不依赖时钟。于是 GET 与 POST 共用同一次 place 级只读，且**仍然不 realize 走路**（`read_move_state` 那个写副作用一并消失）。判据仍是 `place == STORE_LOCATION_ID`（不放宽到店门、`area` 不参与），位置缺失仍 `WORLD_LOCATION_UNAVAILABLE` fail-closed。

**未写 live 的只读证据**（原 `operation_key = 39fa909d-7082-43e4-97b1-3fef55f6ee43`，`/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094/one-day/world-home`，全程只读，未重启、未重买、未 submit）：

```
find_purchase_intent(39fa909d-…)          -> None            # 该 key 没有半笔意图
balance                                   -> 9760
transactions                              -> 只有 1 笔：PURCHASE 240 virtual_snack_wafer（2026-10-18）
items                                     -> virtual_snack_wafer OWNED home/bedroom
deliveries                                -> 无（没有为这次失败建过配送）
world_state.location                      -> home_goods_store / home_goods_store
phone/operations.jsonl (同 key)           -> store_purchase / unavailable / WORLD_LOCATION_UNAVAILABLE
```

用修好的代码对同一 live root 再跑纯读判定（不写任何文件）：`_store_check(...)` → `{"status":"available","availability":{…"WITHIN_OPEN_HOURS"}}`、`_current_world_location(...)` → `home_goods_store`、`store_shop_projection(...)` → `available / at_store=True / purchase_available=True / balance=9760`。即：代码侧已能接受同一位置；正在跑的 serve（12638）进程仍是旧代码，需要父级重启后才生效，**我没有重启**。该 key 无任何已写事实，父级可直接按原 key 重试。

**回归（真实 generated coastal world + timed move，不手写假 `world_state.json`）**：`tests/test_store_room_owner.py::CoastalGeneratedWorldTests`

* `test_a_packaged_world_place_is_read_like_the_shop_get_does` —— `demo.prepare_demo(with_world=True)` + `demo.effective_features(room_living=True)` 生成真实 `coastal_town_929711`；三跳真实 timed travel（`plan_move_to`/`apply_move_to` + 用 travel 记录里的 `expected_arrival_at` 注入时钟后 `travel.realize_due_travel`，绝不伪造时钟冒充）；断言 `_current_world_location` 从 `home` 变成 `home_goods_store`、`PhoneShopping.store()` 报 `applied`/`at_store=true`、`store_purchase` 真扣 4200 且落一笔 OWNED 实例、同 key 重放 `already_applied` 且余额不变。
* `test_standing_at_home_is_refused_without_moving_money` —— 同一个生成世界但留在 `home`：GET 是 `rejected`/`STORE_LOCATION_REQUIRED`（货架仍可读、`at_store=false`、`purchase_available=false`），POST 拒、余额与账本零变化。
* `test_a_missing_world_state_stays_fail_closed` —— 无 `world_state.json`：GET/POST 都是 `unavailable`/`WORLD_LOCATION_UNAVAILABLE`，余额仍 `MOVE_IN_GRANT`，账本 0 笔（位置缺失不伪造、不回落）。

---

## 1. 做了什么（都是真写，不是文档）

### 实体店（同一经济 owner）

* `world_property_economy.store_shop_projection()`：**纯读**货架投影（在店？营业？余额？容量？六件目录 + 每件 `affordable` + `delivery` 模式）。`_read_world_location()` 直读 `world_state.json`，**不**走 `read_move_state`（那会顺手 realize 到达）；`_current_world_location()`（下单路径用的同一个判据）在本轮也改成复用这同一处 place 级只读，见 §0.1。
* `world_property_economy.store_purchase_item()`：柜台下单。服务端 `validate_local_owner`（真 uid / subject / home）→ 渠道校验（只卖 `home_goods_store` 渠道）→ 幂等（`purchase_intent_id = operation_key`）→ 一次原子 `_save()` 同时写 DEBIT 与 OWNED 实例。新增合法 provenance 对 `("local_owner_store_purchase","store_counter_home_delivery")`；**没有** `main_self_verified=True` 捷径。
* 配送：沿用既有 2h 时间窗（`DELIVERY_DURATION_SECONDS` 未改）或即时到家；`delivery` 记录机制名仍是 `temporal_home_delivery`（销售渠道由 transaction provenance 证明）。
* 到货：新公开 `realize_due_deliveries()`，**只**由 `WorldBodyService.tick()` 的第 3 步调用；幂等（已 DELIVERED 不再 due）。`home_items_projection()` 的读路径 realize 已删除。
* `world_phone_shopping.PhoneShopping.store()` / `.store_purchase()`：P 的 `GET /api/phone/store`、`POST /api/phone/store/purchases` 直接落地（P 已接线并通过其 21 项测试）。
* operation key 唯一命名空间：同 key 跨渠道（手机/柜台）、跨商品、跨 world 动词都判冲突（`OPERATION_KEY_CONFLICT` / `OPERATION_TARGET_CONFLICT`），双向都有测试。

### 自有家具布置（不是拿起）

* substrate schema `v1.2 → v1.3`（唯一新增 `fixtures` 块，v1/v1.1/v1.2 内存迁移，placement 结构一字不改）。
* `SURFACE_KINDS = {bed_surface, desk_surface, chair_surface}`；`surface_id` 形如 `desk_surface:<item_instance_id>`，由 `surfaces_here()` 从 **live placement + 经济 catalog** 派生（没有注册表文件，无第二份位置真相）。`desk_surface`/`chair_surface` 不接受裸 id；`bed_surface` 保留裸 id 给既有实例。
* Resolver 新动词 `ARRANGE{object_id,target}`：授权判据 `arrange_verdict()`（OWNED + `relocatable` + 已到货 + 她在家 + 家具在家 + 只能到本宅 area / 自己的 surface），物理写一次原子 save；子物件（放在该家具自己 surface 上的东西）同一次写入里跟着搬家并记 `carried`；同目标 → `noop`。
* `PICK_UP` 语义零改动：床/桌/椅 `takeable=false` 永远拿不起来（有正向+负向测试）。
* 椅子/床的真实姿势/休息条件：`rest_surfaces_here()` 替换原来「只认 SURFACES 键」的 join（resolver 与 gateway 两处都改），`_rest_evidence` 也改成派生 surface 的 `pose_condition`。

### 房间灯（声明 + 记录的 power）

* 新模块 `components/world/scripts/world_room_environment.py`：`FIXTURE_DECLARATIONS`（只 `bedside_lamp`，`dim`）、`derive_light()`（place/area 过滤；缺条目 = `FIXTURE_POWER_UNKNOWN`，无声明 = `NO_DECLARED_LIGHT_IN_AREA`，**unknown 不是 off、不是 0**）、`environment_input()`、`light_for_home()`、`LIGHT_BAND_INTENSITY = {dim:0.20, moderate:0.45, bright:0.70}`。
* Resolver 新动词 `TOGGLE_FIXTURE{object_id,state}`：**绝对** on/off；同 state → `noop`（零写入、revision 不变、无 committed 账本）；只接受已声明可通电 + 真的在当前 place/area 的灯（`NOT_A_FIXTURE` / `FIXTURE_NOT_HERE`）。
* Body：`body_consequence.advance_environment()` 只喂 `ENVIRONMENT.sensory_intensity`，永不产 `temperature_tendency`；速率/阈值/`POSE_STRAIN`/恢复门槛/信号波段零改动。owner tick 一个 tick **至多一次**时间推进；<60s 不动；>900s 记 `environment_gap_unknown` 且不写盘。
* flag `CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED` 默认 deny；`world_body_config.owned_environment()` 只回显部署显式设置的值（GET 不能自改，旧 root 不会因重启被自动开启）。
* 快照仍**九个顶层块**：`local_environment.light`、`objects.surfaces`、`objects.owned_furniture`、`objects.deliveries` 全为块内附加字段（字段名/结构见契约 §3）。Gateway 新增只读 `get_environment`，读命令/动词白名单同步。

## 2. 改动文件

```
M components/world/scripts/world_body_substrate.py      v1.3 fixtures / SURFACE_KINDS / surfaces_here / set_fixture
M components/world/scripts/world_property_economy.py    store 投影+下单 / realize_due_deliveries / arrange_verdict / deliveries_projection / catalog_declaration / `_current_world_location` 改 place 级只读（§0.1）
A components/world/scripts/world_room_environment.py    光声明与派生（新模块）
M components/world/scripts/world_action_resolver.py     ARRANGE / TOGGLE_FIXTURE / surface 目标判据 / 派生 rest surface
M components/world/scripts/world_phone_shopping.py      PhoneShopping.store() / store_purchase() / materialize_owned_item()（唯一物化 seam）
M components/world/scripts/world_phone_world.py         VERBS/PARAM_SPEC/参数校验/replay 证据/四个块内字段/light
M components/world/scripts/body_consequence.py          build_environment_input / advance_environment
M components/world/scripts/world_body_binding.py        owned_catalog_map()
M components/world/service/world_body_service.py        tick 增 deliveries（realize + 物化）/ environment（一 tick 一次推进）/ 透传 fixture|arranged
M components/world/service/world_body_gateway_api.py    get_environment / 两动词 / actions 条目 / rest surface 派生 / 透传
M components/world/service/world_body_config.py         ROOM_ENVIRONMENT_ENABLED_ENV（默认 deny）
A tests/test_store_room_owner.py                        25 项（含 3 项真实 generated coastal world 回归）
A tests/test_room_environment.py                        18 项
A docs/engineering/rc1-room-living-contract.md
A docs/engineering/rc1-room-living-result.md            （本文件）
A .agents/notes/implemented/feature/2026-10-22-rc1-room-living-store-arrange-light.md
```

## 3. 真实测试与门禁（WSL，`PYTHONDONTWRITEBYTECODE=1`，未用 py_compile）

| 命令 | 结果 |
|---|---|
| `python3 tests/test_store_room_owner.py` | **Ran 25 — OK**（到店/关店/钱/权限/key 冲突/GET 零写/到货一次/**store→tick→ARRANGE 全链（无预置 placement）**/instant 配送/幂等 tick/冲突不覆盖/未到货拒/ARRANGE 不造持有/跨 place-area 拒/desk instance/子物件随迁/灯 unknown 与幂等/非 fixture 拒 + **3 项真实 generated coastal world 回归**：打包世界 place 读法、真实三跳 timed travel 到店首买、在家拒买零钱、无 world_state fail-closed） |
| `python3 tests/test_room_environment.py` | **Ran 18 — OK**（unknown≠off、area 过滤、band 表、无温度、dim 不触发 elevated、`anchor=max(last_advanced_at, since)`、旧 root 开灯不被历史 gap 卡死、gap 只推时钟不喂光、一 tick 一次推进） |
| `python3 components/world/service/test_world_body_service.py` | Ran 39 — OK |
| `python3 tests/test_phone_world_owner.py` | Ran 20 — OK（九块形状不变） |
| `python3 tests/test_phone_shopping_owner.py` | Ran 21 — OK |
| `python3 tests/test_world_body_life.py` | Ran 27 — OK |
| `python3 tests/test_world_consume.py` | Ran 31 — OK |
| `python3 tests/test_phone_api.py` | Ran 13 — OK |
| `python3 tests/test_phone_tool.py` | Ran 11 — OK |
| `python3 tests/test_phone_world_tool.py` | Ran 7 — OK |
| `python3 tests/test_room_living_api.py`（P 新） | Ran 21 — **OK**（P 的接线按本契约通过） |
| `python3 tests/test_room_living_tool.py`（P 新） | Ran 16 — **OK** |
| `python3 tests/test_phone_world_api.py`（P） | Ran 9 — **OK**（P 已把 `local_owner_verbs` 断言放宽为包含判据） |
| `node --experimental-strip-types .../verify-agent-note-tree.ts`（repo cwd） | exit 0，`ok: 41 note(s) tree and relative links verified` |
| `node --experimental-strip-types .../verify-agent-note-format.ts`（repo cwd） | exit 0，`ok: 41 note(s) verified` |
| `git diff --check` | exit 0 |

## 4. 已知红 / 让位点（如实记录）

* 原「已知红」`tests/test_phone_world_api.py:242`（五动词精确断言）已由 P 自己放宽成「必须包含」判据（该文件第 245–250 行注释说明白名单属 C 的契约 §2），本轮复跑 `Ran 9 — OK`，C 未改该文件。
* 现场 503 的修复在**代码**里生效，但父级那个正在跑的 `serve`（12638）是修复前启动的进程，重启之前仍会按旧逻辑返回 `WORLD_LOCATION_UNAVAILABLE`；C 没有重启它。
* 未做真实浏览器页面验收、未在运行中的 live 实例（9760 / 原小点心）上操作任何数据，也未重启任何服务。

## 5. 边界与不能声称的东西

家具没有手持形态（`takeable=false` 不变）；无容器/书籍内容；灯只有 `dim` 一档声明，且 `dim` **不**触发 `sensory_load_elevated` 是正确结果（未调亮、未改速率/阈值）；没有温度/噪音/天气/日照推算，没有自动作息或自动关灯；不是全城经济、没有 3D/NPC；未做真实模型、真实支付、外发、生产切换、发布。
