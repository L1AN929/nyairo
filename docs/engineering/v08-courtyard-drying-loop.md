# v0.8 Wave 2 · 院子晾晒 / 盆栽 / 纸盒手作 / 街角借还自主循环

本文件说明 v0.4 邻里生活引擎如何进入 v0.2/v0.3 自主日常循环。所有写入仍由既有 owner
契约完成，本桥接层只做"真实状态 → 候选 → 确定性动作 → 幂等结算"。

## 1. 状态与所有权

- 唯一状态文件：`<day_root>/world/v04_neighborhood_state.json`
  （`v04_neighborhood_living.state_file_path`）。
- 写入口只有一个：`components/phone/v04_neighborhood_view.mutate_v04_neighborhood_view`
  （字段白名单 / actor_side / `operation_key` 幂等 / `expected_revision` CAS）。
- 只读入口：`read_v04_neighborhood_view`；`partner_default` 视角隐藏未公开卡片正文与全部草稿。

## 2. 六类日常候选（`collect_v02_v03_loop_candidates`）

| source_ref | 触发（真实状态） | 动作 | 负荷标注 |
|---|---|---|---|
| `v04_courtyard_hang:<item_id>` | 布巾在 `home_rack` 且 `wet`/`damp`；晴天→`yard_line`，微雨→`porch_hook` | `hang_or_collect_cloth` `hang` | 不出门、非创作 |
| `v04_courtyard_cloth:<item_id>` | 布巾在露天 `yard_line` 且天气 `rain/drizzle/...` → 抢收 `porch_hook` | `hang_or_collect_cloth` `move` | 不出门、非创作 |
| `v04_courtyard_collect:<item_id>` | 布巾在露天/门廊且 `moisture <= 5.0`（dry）→ `home_rack` | `hang_or_collect_cloth` `collect` | 不出门、非创作 |
| `v04_porch_plant:<plant_id>` | 浇水：`hydration <= 35`；搬位：盆栽在 `yard` 且雨/微雨 → `porch` | `water_or_move_plant` | 不出门、非创作 |
| `v04_home_craft:<box_id>` / `:<card_id>` | 纸盒步骤未到 `finished/set_aside`，或存在未誊写草稿卡片 | `craft_paper_or_box` | 不出门、**创作高负荷** |
| `v04_errand_return:<item_id>` | 共享架工具或旧书店图书 `status=borrowed` 且 `holder=subject` | `shared_shelf_loan` `return` / `bookstore_service` `return_book` | **需要外出**、非创作 |

闭店时段（`10:00–19:00` 之外）还书描述符带 `via_return_box=True`，结算走还书箱
（`return_box_pending`，`shopkeeper_received=False`）。

候选描述符同时携带 `v04_action` / `v04_payload` / `v04_action_kind`，供结算直接使用。

## 3. 零写入保护

`collect_v02_v03_loop_candidates` 与时钟推进都先看状态文件是否存在：

- 不存在：零 `v04_*` 候选、零目录、零文件、零 mtime 变化。
- 存在：`load_state_readonly` 只读解析；采集阶段绝不落盘。
- 旧投影形状（`weather_and_ground` / `courtyard.drying_rack`）保留为只读兼容分支。

## 4. 结算（`settle_v02_v03_round_consequences`）

接受三种入口：

1. `{"v04_action": "craft_paper_or_box", "v04_payload": {...}}`（resident 自动结算用）
2. `{"action": "v04_courtyard_hang:<id>", ...}` 字面快捷方式
3. `{"action_kind": "HANG_DRYING_CLOTH" | "RESCUE_DRYING_CLOTH" | "COLLECT_DRIED_CLOTH" |
   "CARE_PORCH_PLANT" | "ADVANCE_HOME_CRAFT" | "RETURN_NEIGHBORHOOD_LOAN", ...}`

统一调用 `mutate_v04_neighborhood_view(..., actor_side="subject", now_iso=...)`。返回非 200
即 `REFUSED`，零乐观写入。桥接 `operation_key` 与 v0.4 引擎回执双重防重放，重复结算返回
`already_applied=True`，布巾/纸张/工具/图书数量守恒。

## 5. 时钟分段推进

- `ADVANCE_CLOCK` / `CROSS_NIGHT_ADVANCE`（或带 `advance_clock_to_iso`）结算时调用
  `advance_v04_neighborhood_processes(now_iso=target)`：晴天按支撑位蒸发、雨天暂停或淋湿、
  盆栽按位置失水/生长，绝不伪造未执行过的动作。
- `run_v02_v03_daily_tick(advance_clock=True)` 同样把 v0.4 分段推进到本 tick 时刻，
  报告在 `clock_evaporation.v04_neighborhood`。

## 6. 与 Wave 1 健康门联动

- `v04_home_craft.creative_intensive=True` → 急性发烧（`creative_block`）时被 AG-1
  `HARD_REJECT:BODY_CREATIVE_BLOCK` 拒绝；病愈后候选自然重新出现。
- `v04_errand_return.requires_outing=True` → `blocks_strenuous_outing` 时被候选门过滤，
  不带病出门。
- `v04_courtyard_cloth`（遇雨抢收）`requires_outing=False`，重病时仍可在家门口执行。

## 7. 验收

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest \
  tests.test_v04_neighborhood_living tests.test_v08_courtyard_drying_loop \
  tests.test_body_health tests.test_body_health_projection tests.test_health_cap_wiring \
  tests.test_v08_health_decision_chain tests.test_v08_health_candidate_policy \
  tests.test_somatic_comfort_view -v
python -m unittest tests.test_v02_v03_causal_counterfactual_soak -v
```
