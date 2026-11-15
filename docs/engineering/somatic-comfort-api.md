# 具身冷暖 / 窗扇与外衣 / 桌边照顾 API（somatic comfort slice）

Status: implemented（2026-11-11，0.3 第一弹）

三个端点，一个本地仿真状态文件。边界按设计 33/34/37：

1. **世界环境物件**：真实 `PhoneWorld.snapshot()` 的当前 place/area、灯光、当天 ambient 模板声景；
2. **Body 姿态负荷**：真实 Body 的 pose / orientation / recovery / needs 档位 / signals / rest_evidence；
3. **有界感觉投影**：只有 `thermal/moisture/posture/tactile` 四类，有限 quality/salience/region/source_refs；
4. **认知解释选择**：不在本模块。投影不写 Body、不声称主观感受、不把报错/延迟/额度映射疼痛。

窗扇、局部位置、外衣、温饮/靠垫都是**本实例局部仿真条件**（`phone_slice_simulation`）。
没有 World owner 字段可复用；它们不是世界测量，不复制 Body/世界位置，不另建动作/权限账本。
真实饮用（如果绑定了真实可消费实例）只走既有 owner 的 `PhoneShopping.use` → Resolver
`CONSUME` → Body 后果，不在这里减 thirst、编 receipt 或签新权限。

## 通用契约

- 所有端点都过现有 `_guard_common`（Host / Origin / Sec-Fetch-Site）。
- 三个 GET 默认允许**无凭据**的安全只读视图；一旦显式携带 `Authorization: Bearer <token>`
  或 `X-Phone-Capability`，就必须按本次 serve 的本地 capability 做既有常量时间校验：
  错误 / 不可用一律 403（`CAPABILITY_INVALID` / `CAPABILITY_UNAVAILABLE`），
  **绝不降级**回默认只读。
- 所有 POST 先过 `_guard_mutation`（CSRF）。本人动作再要本次 serve 的本地 capability
  （`Authorization: Bearer <token>` 或 `X-Phone-Capability`）。错误/缺失/不可用一律 403，
  **不会降级**成“伴侣”或“只读”。
- `X-Phone-Actor` 只是声明，不是身份；没有 capability 的 `her` 动作仍然 403。
- 字段白名单按**动作存在性**校验：该动作不接受的字段，即使值是 `[]` / `null` / `""`
  也按存在拒绝（400 `UNKNOWN_FIELD:<name>`）。必需字段为空/缺失为 400。
- 明显属于 owner 的字段（`path`、`url`、`item_instance_id`、`object_id`、`temperature`、
  `elapsed`、`pose`、`place_id`、`area_id`、`operation_key` 等）不在请求体里接受。

本地状态：`<root>/phone/somatic_comfort.json`，`schema_version=1`，只记录本单实际窗/衣物/
照顾条件、局部位置引用、revision 与时间。缺失 = 全未知；损坏 = fail closed（只读返回
`SOMATIC_STATE_UNREADABLE/INVALID`，绝不写回）。GET 缺文件不建目录。

## GET /api/phone/body/somatic

纯读聚合：真实 World 快照 + 真实 Body 投影 + 本实例仿真条件。零写入、零模型、零计时器、
零自动 Activity。

```json
{
  "ok": true, "status": "applied",
  "data": {
    "schema": "phone.body.somatic.v1",
    "read_only": true,
    "observed_at": "...",
    "revision": 3,
    "local_state": {"status": "available|unavailable", "reason_code": null},
    "body": {"status": "...", "pose": "...", "orientation": "...", "recovery": {...},
             "signals": [...], "needs": {...}|null, "needs_status": "available|unknown",
             "rest_evidence": "..."|null, "bands_only": true, "raw_values_exposed": false},
    "world": {"place_id": "...", "area_id": "...", "status": "...", "reason_code": null,
              "local_date": "...", "local_time": "...", "light": {...},
              "world_revision": 7, "source": "world_phone_world.snapshot"},
    "ambient": {"available": ..., "events": [...], "template_projection": true,
                "is_weather": false, "not_evidence_of_wetness": true},
    "weather": {"value": null, "status": "unavailable",
                "reason_code": "NO_WEATHER_AUTHORITY", "is_weather_authority": false},
    "modalities": {
      "thermal": {...}, "moisture": {...}, "posture": {...}, "tactile": {...}
    },
    "modality_count": 4,
    "available_modalities": ["thermal", "posture"],
    "unknown_modalities": ["moisture"],
    "temporal_alignment": {"atomic_snapshot": false, "status": "partial",
                           "reason_code": "INDEPENDENT_OWNER_READS"},
    "layers": { "world_environment": {...}, "body_pose_load": {...},
                "bounded_projection": {"writes_body": false,
                  "claims_subjective_experience": false, "maps_errors_to_pain": false},
                "cognition_choice": {"in_scope": false} },
    "simulation_sources": ["phone_slice_simulation"],
    "raw_body_values_exposed": false, "private_text_included": false, "local_only": true
  }
}
```

每个 modality 固定字段：

```json
{"modality": "thermal", "status": "available|unknown|none",
 "quality": "...|null", "salience": "none|low|medium|high",
 "region": {"place_id": "...", "area_id": "...", "local_zone": "window_side|desk_side|elsewhere|unknown",
            "local_zone_source": "phone_slice_simulation", "area_source": "world_phone_world.snapshot",
            "bound": {"place_id": "...", "area_id": "..."}, "at_declared_position": true},
 "source_refs": ["..."], "simulation": false, "reason_code": null,
 "claims_subjective_experience": false, "diagnoses_pain": false}
```

有限词汇与规则：

| modality | 证据 | quality |
| --- | --- | --- |
| `thermal` | 只有本人显式 `local_position.zone=window_side`、绑定同一 place/area、且窗 `open/ajar` 才是 `cool`。关窗 → `status=none`，`WINDOW_CLOSED_NO_THERMAL_SOURCE`。位置未知/非窗侧 → `status=unknown`，`NO_WINDOW_PROXIMITY_EVIDENCE`（不把卧室名字等同窗边，也不说“全家冷”）。 |
| `moisture` | 只认外衣 `wear` + 本人显式 `dryness=damp`。挂起 → `OUTERWEAR_NOT_WORN`；未声明 → `OUTERWEAR_NOT_DECLARED`。ambient 雨声**不是**淋湿证据。 | `damp` / `dry` |
| `posture` | 真实 Body pose + 真实 `rest_evidence`；靠垫使用时另加本地支撑来源。**没有姿态持续证据**：`sustained_duration_seconds=null`、`NO_POSTURE_DURATION_EVIDENCE`，绝不宣称“久坐”；`pain_claim=null`、`NO_PAIN_AUTHORITY`。 | `supported_world_surface` / `supported_local_cushion` / `seated_support_unverified` / `lying` / `standing` / `transitioning` |
| `tactile` | 温饮 `available` 只表示 `reachable_warm_object`；本人 `take` 之后才是 `held_warm_contact`；`consumed` 后无信号。温度回室温时只给 `reachable_room_temp_object`。 | `reachable_warm_object` / `held_warm_contact` / `reachable_room_temp_object` |

没有证据就 `unknown/none`，不为了凑四类编事实。`temporal_alignment` 永远如实标
`partial`：World、Body、本地状态是独立读，不假一次原子快照。

## GET / POST /api/phone/body/garment-window

### GET

纯读。返回 `schema=phone.body.garment_window.v1`：

- `window`: `{state: open|ajar|closed|null, updated_at, source, simulation}`；
- `local_position`: `{zone, bound_place_id, bound_area_id, declared_at, at_declared_position, source}`；
- `garment`: `{status, state: wear|hang|null, dryness: dry|damp|unknown, garment_id, label,
  hang_position, dampness_source, drying, source}`；
- `mutual_exclusion`: `{wearing_and_hanging_impossible: true, states, dryness_states}`；
- `drying`: `{supported, only_declared_offline_drying, rate_seconds: 21600,
  derived_from_elapsed: true, writes_progress: false}`；
- `weather` 永远是 `NO_WEATHER_AUTHORITY`。

干燥规则：本人把 `damp` 外衣挂起后，保存 `started_at` 与固定上限 6 小时；GET 按本次业务钟
elapsed 派生 `damp → dry`，**不落盘进度**。重复同一挂起意图不重置 `started_at`、不升 revision；
时钟回退 → `dryness=unknown`、`drying.reason_code=CLOCK_ROLLBACK`，不“瞬干”。重开同 root 状态连续。
只支持声明的离线干燥，不补写她离线做过什么。

### POST

只属于本人：CSRF + capability。不开放伴侣替她开关窗或换衣。

| action | 必需字段 | 可选字段 | 说明 |
| --- | --- | --- | --- |
| `set_window` | `state` ∈ open/ajar/closed | — | 同状态幂等，不升 revision |
| `set_local_position` | `zone` ∈ window_side/desk_side/elsewhere | — | 服务端现读当前 place/area 作为绑定；位置不可读 → 409 `LOCAL_POSITION_UNREADABLE`；在途 → 409 `LOCAL_POSITION_UNSTABLE` |
| `wear_outerwear` | `garment_id` | `label`(≤40), `dryness` ∈ dry/damp/unknown | 穿与挂互斥；穿时清空挂位与 drying |
| `hang_outerwear` | `garment_id`, `hang_position` | `dryness` | 稳定对象身份 + 挂架位置绑定；同意图幂等；不重置 drying_start |

新实例未设定就是 `unknown`，不默认全湿/全干。`dampness_source` 只能是
`self_declared_simulation`。成功返回 `{action, changed, revision, updated_at, window|garment|local_position}`；
同意图返回 `status=already_applied`、字节不变。

错误：400 `UNKNOWN_FIELD:*` / `WINDOW_STATE_INVALID` / `LOCAL_ZONE_INVALID` /
`GARMENT_ID_REQUIRED` / `HANG_POSITION_REQUIRED` / `GARMENT_DRYNESS_INVALID`；
403 CSRF/capability；409 位置不可读/不稳定。

## GET / POST /api/phone/body/care-offering

最多 1 个饮品槽（`warm_water` / `hot_tea`）+ 1 个靠垫槽（`floor_cushion`）。

### GET

纯读，`schema=phone.body.care_offering.v1`：

```json
{
  "drink": {"slot":"drink","kind":"warm_water|hot_tea","actor_side":"her|partner",
            "position":"hand_side|desk_side","state":"available|taken|consumed|stowed|withdrawn",
            "bound":{"place_id":"...","area_id":"..."}, "location_match": true,
            "available_here": true, "taken": false,
            "temperature": {"band":"warm|tepid|room_temp|unknown",
                            "placed_at":"...", "initial":"warm", "final":"room_temp",
                            "cooling_seconds":1800, "elapsed_seconds":..., 
                            "derived_from_elapsed":true, "never_rewritten":true,
                            "source":"phone_slice_simulation"},
            "bound_item": {"item_instance_id":"...","label":"..."}|null,
            "drink_supported": false,
            "drink_reason_code": "NO_THIRST_RELIEVING_CONSUMABLE",
            "receipt_ref": null, "pending_unknown": false,
            "changes_needs": false, "source": "phone_slice_simulation"},
  "cushion": {"slot":"cushion","state":"available|in_use|stowed|withdrawn",
              "support_active": false, "support_reason_code": "POSTURE_CHANGED|LOCATION_CHANGED",
              "support_rechecked": true, "changes_needs": false, ...},
  "slots": {"drink": 1, "cushion": 1},
  "changes_needs": false, "changes_relationship": false, "changes_activity": false,
  "auto_follows_location": false, "drink_is_existing_owner_consume": true
}
```

- **位置绑定**：服务端在 `offer` 时现读真实当前 place/area，存局部绑定；不自动跟随跨房移动。
  GET 每次现读比较，`location_match=false` 就如实说不在原位。
- **冷却**：`placed_at` + 固定 30 分钟曲线持久；GET 按 elapsed 派生 `warm → tepid → room_temp`，
  不刷新、不因聊天结束瞬变、时钟回退 → `unknown`，不回退重热。重复同内容 offer 不重置放下/冷却时间。
- **靠垫**：放下只表示 `available`；本人 `use` 且真实 pose=`seated`、仍在绑定 place/area 才
  `support_active=true`。坐姿或区域改变后 GET 重核为 false（`POSTURE_CHANGED` /
  `LOCATION_CHANGED`），不远程支撑。

### POST

| action | 身份 | 字段 | 语义 |
| --- | --- | --- | --- |
| `offer` | `actor_side=partner` 仅 CSRF；`actor_side=her` 需 CSRF+cap | `slot`, `kind`, `actor_side`, `position` | 服务端绑定真实当前 place/area；`position=desk_side` 需要当前区域真实声明过 table/桌面，否则 409 `NO_DECLARED_DESK_SURFACE`。位置不可读/在途 → 409 `LOCAL_POSITION_UNREADABLE` / `LOCAL_POSITION_UNSTABLE`。同内容幂等；她已动过的槽不能被替换（409 `CARE_SLOT_LOCKED_BY_SUBJECT`）。**换新份规则**：槽进入 `consumed/stowed/withdrawn` 终态后才允许下一次 `offer`，同一槽只保留一条记录，不累积历史垃圾。 |
| `take` | CSRF+cap | `slot=drink` | `available → taken`；位置不在绑定处 → 409 `LOCATION_CHANGED`。拿起不等于饮用。 |
| `drink` | CSRF+cap | `slot=drink` | 必须先是 `taken`。只有 offer 绑定了**真实、当前可用、声明了 thirst 效果**的可消费实例时才调用既有 `PhoneShopping.use`（key = `somatic-drink:<offer_id>`，重放同一动作不二次摄入）。否则返回 409 `DRINK_UNSUPPORTED`（`detail=NO_THIRST_RELIEVING_CONSUMABLE`），本地**不**标 consumed、不编 receipt、不自行减 thirst。owner 返回 `unknown` 时保持 taken，重放同动作用同一 key 对账，绝不换 key 重试。 |
| `use` | CSRF+cap | `slot=cushion` | 真实 pose 非 seated → 409 `POSTURE_NOT_SEATED`；位置不符 → 409 `LOCATION_CHANGED`；合法才 `available → in_use`。 |
| `stow` | CSRF+cap | 任一槽 | 把 offer 收起，不消费；已终态 → 409 `CARE_ALREADY_CLOSED`。 |
| `withdraw` | partner（CSRF；presented cap 仍需有效） | 任一槽 | 只能撤回 `actor_side=partner` 且 `available`（她尚未动过）的 offer；她动过 → 409 `SUBJECT_HAS_TOUCHED`；她自己的 offer → 409 `WITHDRAW_NOT_OWNER`。 |

错误：400 字段/取值非法；403 CSRF/capability；409 业务拒绝（含 `DRINK_UNSUPPORTED`）；
202 owner 结果 unknown。所有本地动作只写 `phone/somatic_comfort.json`，不写 Body/关系/
Activity/Memory/阅读断点；只有 `drink` 的合法分支会进入既有 World/Body owner。

## 来源归属

| 事实 | 来源 |
| --- | --- |
| 当前 place/area、灯光、ambient 声景、Body pose/recovery/signals/needs | `world_phone_world.snapshot()` / `world_body_gateway` |
| real rest surface | Body `rest_evidence`（World 测量） |
| 窗开合、局部位置、外衣、干燥、温饮/靠垫、冷却、支撑 | `phone_slice_simulation`（本实例 JSON） |
| 真实饮用 | `world_phone_shopping.use` → Resolver `CONSUME` → Body 后果（仅绑定真实可消费实例时） |
| 天气 | 无权威：`null` + `NO_WEATHER_AUTHORITY` |
| 姿态持续时间 | 无权威：`null` + `NO_POSTURE_DURATION_EVIDENCE` |

## 已知未覆盖

- 生产目录没有“饮品”可消费实例：本切片 fixture 里没有绑定真实饮品，`drink` 走
  `DRINK_UNSUPPORTED` 负控。支持的代码分支保留（绑定真实 thirst 可消费实例 + 既有
  `PhoneShopping.use`），但本 fixture **未验证真实饮用 receipt/exactly-once**。
- 没有全身温度测量、没有气象权威、没有姿势持续时长；这些一律 unknown，不编数值。
- 不证明真模型自然采用、不证明 24h 连续生活、不证明多真人任务隔离。
