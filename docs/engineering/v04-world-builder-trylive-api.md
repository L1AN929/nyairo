# v0.4 世界编辑诊断与试住一小时隔离沙盒 API 契约（`v04_builder_trylive`）

本文件定义本地手机/桌面服务端承载 `CHIYO-总体设计-v0.4` 第 `44` 章（《世界编辑、内容生产与试住工具》）五大核心能力的确定性、零模型调用、绝对沙盒隔离接口契约：

- **建议挂载路由**：`GET /api/phone/v04/builder-trylive` & `POST /api/phone/v04/builder-trylive`
- **响应 Schema**：`phone.v04.builder_trylive.v1`
- **隔离沙盒引擎**：[`v04_world_builder_trylive.py`](../../components/world/scripts/v04_world_builder_trylive.py)（仅允许写入 `<day_root>/phone/v04_builder_sandbox.json`，`0600` 原子替换落盘，拒绝符号链接）
- **HTTP 视图与动作闸门**：[`v04_builder_trylive_view.py`](../../components/phone/v04_builder_trylive_view.py)（暴露 `read_v04_builder_trylive_view` 与 `mutate_v04_builder_trylive_view`）

---

## 1. 核心安全与沙盒隔离铁律（第 `44` 章）

1. **零污染正式生活状态**：路线预演、场景缺口草案、试住一小时、因果溯源与修改影响预览全程运行在内存深拷贝副本中，**严禁改写 `<day_root>/world/`、角色生活记忆、私人日记或私人作品**。
2. **`GET` 100% 零磁盘写入**：无论 `<day_root>` 是否存在或是否初始化了 `world/v04_neighborhood_state.json` 与 `phone/v04_builder_sandbox.json`，`read_v04_builder_trylive_view` 均不创建任何目录或临时文件，不改变任何既有文件 `mtime`。
3. **确定性规则执行**：全链路 `llm_calls == 0`，默认主体代词使用中性 `"TA"`。
4. **双视角认知与隐私边界**：`inspect_why_changed` 并排输出建设者诊断 `builder_view` 与角色有限感知 `subject_bounded_view`（角色不会自动获知后台失水公式与管理解释；建设者工具同样不读取、不暴露角色私人日记与未公开心事，缺失历史统一诚实标注 `"无更早记录，不补写推测原因"`）。

---

## 2. 标准 Python 接口签名与 `GET` 响应结构

```python
from components.phone.v04_builder_trylive_view import (
    V04_BUILDER_TRYLIVE_SCHEMA,
    mutate_v04_builder_trylive_view,
    read_v04_builder_trylive_view,
)

# GET（100% 零磁盘写入）
status_code, envelope = read_v04_builder_trylive_view(
    day_root,
    has_subject_capability=False,
    now_iso=optional_now_iso,
)

# POST（仅写 <day_root>/phone/v04_builder_sandbox.json）
status_code, envelope = mutate_v04_builder_trylive_view(
    day_root,
    payload,
    actor_side="partner",  # 支持 "partner" 与 "subject"，其余返回 403 FORBIDDEN_ACTOR_SIDE
    now_iso=optional_now_iso,
)
```

`GET` 返回 `(200, envelope)`，顶层与 `envelope["data"]` 同步提供桌面端锁定键与领域契约键：
- 元信息：`schema` (`"phone.v04.builder_trylive.v1"`)、`read_only` (`True`)、`perspective`、`revision`、`observed_at`、`identity`、`sandbox_isolated` (`True`)、`llm_calls` (`0`)。
- 诊断与配置列表：
  - `route_audit` / `last_route_audit`：当前三圈街区连通性审计或最近一次路线预演结果；
  - `presets`（桌面端锁定 4 项：`go_out_and_return`、`yard_rain_rescue`、`public_notice_and_borrow`、`quiet_desk_afternoon`）与 `preset_scenarios`（领域 4 项：`sunny_cloth_drying`、`rain_midway_drying`、`roadblock_turnback`、`bookstore_ruler_return`）；
  - `inspectable_objects`：`linen_cloth`、`public_notice`、`shared_ruler`、`porch_plant`、`crafted_box`；
  - 最近沙盒记录：`last_rehearsal`、`last_scene_gap_check` / `last_prose_check`、`last_trylive_run`、`last_why_changed`、`last_impact_preview` / `last_modification_preview`。

---

## 3. 五大 `POST` 动作与严格字段白名单

- 未知字段、空键名或伪造身份/计分字段（如 `uid`、`authority`、`owner`、`subject`、`price`、`balance`、`source`、`actor`、`artifact_id`、`actor_side`、`mood_delta`、`affection_score` 等）一律返回 `400 INVALID_FIELDS`。
- 非法 `actor_side` 一律返回 `403 FORBIDDEN_ACTOR_SIDE`。
- 所有动作均支持 `operation_key` 幂等重放（同参数返回 `200` + `already_applied=True` / `command_status="DUPLICATE"`；同 key 改参返回 `409 OPERATION_KEY_CONFLICT`）与 `expected_revision`（失配返回 `409 REVISION_CONFLICT`）。

| `action` | 桌面端锁定字段 | 兼容领域/测试扩展字段 | 核心行为与不变量 |
| --- | --- | --- | --- |
| `audit_routes_or_rehearse` | `route_id`（可选，留空审计全图）、`blocked_edges`（可选 `list[str]`）、`operation_key` | `rehearse_route_id`、`start_place_id`、`destination_place_id`、`route_path`、`inject_roadblock_route`、`on_roadblock`、`custom_places`、`custom_routes`、`object_templates`、`available_keys` | 检出 `isolated_places`、`one_way_traps`（绝不自动补反向边）、`missing_key_doors`；隔离预演中点封路局部感知与安全折返，`mutated_live_resident=False` |
| `check_scene_prose_gaps` | `prose`（必填中文场景描写）、`place_id`（可选，默认 `"porch"`）、`operation_key` | `scene_prose`、`text`、`place_override` | 输出待审 `draft_candidates`（`applied_to_live_world=False`）与逐项 `gaps`（`missing_affordance \| missing_content \| dangling_exit \| missing_object \| ready`） |
| `run_trylive_hour` | `preset_id`、`injections`（元素取自 `rain`、`seat_taken`、`roadblock`、`restart_gap`）、`operation_key` | `route_preset`、`scenario_id`、`inject_faults`（支持 `seat_occupied`）、同名布尔字段、`compare_lighting` (`"bright_vs_glare" \| "none"`) | 内存隔离运行 60 分钟，验证分段晾晒、子物件随包零漂移、闭店归还箱不吞物且不伪造店主签收、双光照版本观察差异，`polluted_live_memory=False` |
| `inspect_why_changed` | `object_id`（`linen_cloth \| public_notice \| shared_ruler \| porch_plant`）、`operation_key` | `target_kind`（含 `wet_cloth \| potted_plant \| crafted_box`）、`target_id` | 只读联动 `world/v04_neighborhood_state.json`，返回 `builder_view` 与 `subject_bounded_view`，`hallucinated_backstory=False` |
| `preview_world_modification` | `mod_kind`（`move_bench \| delete_place \| block_route`）、`target_id`、`to_place_id`、`operation_key` | `mod_kind` 扩展支持 `move_object \| apply_worldpack_diff`；`resolution_mode`、`simulate_occupied_by`、`simulate_items_on_surface`、`simulate_bound_agreement`、`worldpack_diff` | 占用中禁瞬移（`wait_until_idle` / `admin_migrated` 且不造伪记忆）；被引用地点拦截删除（`blocked_referenced_place`）；世界包增量更新保留已生活状态 |

---

## 4. 自测执行命令（WSL）

```bash
/root/nyairo-v051/vendor/hermes/.venv/bin/python -m unittest discover -s /mnt/d/nyairo-rc1-body-backend/tests -p "test_v04_world_builder_trylive.py" -v
```
