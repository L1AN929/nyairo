# `v0.3` 具身厨房消耗守恒、双手物理与邻里生态 API 契约

- **挂载路径**：`/api/phone/v03/embodied-social`
- **领域引擎**：`components/world/scripts/v03_embodied_npc_ecology.py`（Schema: `world.v03.embodied_npc_ecology.v1`）
- **HTTP 视图封装**：`components/phone/v03_embodied_social_view.py`（Schema: `phone.v03.embodied_social.v1`）
- **状态文件**：`<day_root>/phone/v03_embodied_social.json`（`0600` 原子写盘，拒绝符号链接；纯 `GET` 100% 零磁盘写入）

---

## 一、设计对应与核心不变量

1. **厨房储物柜整数守恒与热食/茶饮消耗（`Ch. 13–14` + `ADR-044`）**
   - 所有食材库存 `units` 为非负整数（`total_restocked - total_cooked == units`），初始库存为 `0`，绝不凭空生成食物、绝不出现负库存。
   - `cook_or_brew` 按整料扣减库存；任一食材不足即原子拒绝并返回 `409 INSUFFICIENT_PANTRY_STOCK`。
   - 成品餐食/茶饮（`serving_kind: "drink" | "meal"`）按流逝时间纯函数派生温度档位：`hot`（`< 15m`）→ `warm`（`15m..45m`）→ `cool`（`>= 45m`），跨夜餐食（`8h..24h`）转入 `overnight_needs_reheat`（需先 `reheat_dish`），茶饮满 `12h` 或餐食满 `24h` 转入 `spoiled`（仅可 `discard_dish` 丢弃且 `recorded_as_intake: False`）。
   - `consume_dish` 扣减 `1` 份 `portions_remaining`，归零后再次调用返回 `409 DISH_EMPTY`；生成真实解渴/果腹回执，并在 `somatic_comfort.json` 存在待饮茶水槽时自动核销，闭环饮品消费缺口。

2. **伴侣具身在场、左右手槽物理预留与日常身体接触状态机（`Ch. 15–17` + `ADR-045` + 审阅补充 `§5.1, §5.8`）**
   - `set_partner_presence`（`absent | same_room_apart | beside_at_desk | beside_on_sofa`）恒保证 `foreground_activity_interrupted: False`、`spawns_second_foreground: False`，用户进屋入座绝不强行打断主体手头阅读或手作。
   - 双手槽位物理互斥（`hand_slots: {"left_hand": ..., "right_hand": ...}`）：
     - `0` 手槽动作（需近旁可达）：`sit_close`、`brush_shoulder`、`lean_shoulder`、`forehead_touch`；
     - `1` 手槽动作：`hold_one_hand`（若双手均拿物件返回 `409 HAND_SLOT_OCCUPIED`）；
     - `2` 手槽动作：`two_hand_interlock`、`hug`（任一手拿着杯子/笔均返回 `409 HAND_SLOT_OCCUPIED`）；
     - 不在近旁（`absent` 或 `same_room_apart`）发起接触返回 `409 NOT_WITHIN_REACH`。
   - `rigid_phase_ladder_forbidden: True`：摒弃 1→2→3→4 闯关锁；双方独立同意后才建立接触并占用对应手槽（期间双手端锅同样返回 `409 HAND_SLOT_OCCUPIED`）；主体处于急性生病、独处空间请求或忙碌状态时可自主 `decline_busy` 温和婉拒且零好感扣分；任一方调用 `release_contact` 无条件即刻松开并释放手槽，零退出债。

3. **小镇街坊 NPC 独立作息、私有知识隔离边界与个人风格迁移（`Ch. 24, 26, 27` + 审阅补充 `§5.11, §6.4`）**
   - 零写读取实例真实世界主题（`coastal_town` / `mountain_town` / `residential_town` / `rural_farming`，未配置则回退 `gentle_neighborhood`），确定性生成 4 位有名字、职业、独立手头事务与营业时间窗的街坊熟人；非营业时段当面造访返回 `409 NPC_CLOSED_OR_AWAY`。
   - 每条街坊知晓事项必带 `{fact_id, npc_id, source_type, epistemic_basis, known_at, revoked}`，严格区分亲眼所见（`firsthand_seen`）与二手听说（`secondhand_heard` + `hearsay_from`）。
   - **硬隐私防火墙**：传入私人日记（`diary`）、未发草稿（`DRAFT`）、伴侣私聊（`partner_chat` / `dm`）、`36B` 成年亲密状态或私密可见度材料一律拦截并返回 `403 PRIVATE_MATERIAL_ISOLATED`。
   - 支持 `revoke_npc_shared_ref` 即刻撤回曾给街坊看过的作品引用（标 `revoked: True` 并抹除正文摘要）。
   - 所有 `npc_quote` 强制标记 `text_origin: "untrusted_npc_speech"`、`can_escalate_privilege: False` 并剥离控制指令与提示注入载荷。
   - `style_migration` 支持 `active_curiosity` → `tried_in_work` → `shifted_or_cooled` 自然流转与三分钟热度冷却（`xp_tree_forbidden: True`、`cooling_is_not_failure: True`、`failure_penalty: 0`）。

---

## 二、Python 接入签名

```python
from pathlib import Path
from typing import Any, Mapping
from components.phone.v03_embodied_social_view import (
    read_v03_embodied_social_view,
    mutate_v03_embodied_social_view,
)

# 1. 纯读投影（100% 零磁盘写入）
status_code, body = read_v03_embodied_social_view(
    day_root,
    has_subject_capability=False,  # False -> "partner_default" 脱敏视角；True -> "subject" 完整视角
    now_iso=None,
)

# 2. 状态变更（严格字段白名单 + 发起方权限门禁 + 可选 operation_key 幂等与 expected_revision CAS）
status_code, body = mutate_v03_embodied_social_view(
    day_root,
    payload,
    actor_side="partner",  # "partner" | "subject"（兼容 "her"）
    now_iso=None,
)
```

---

## 三、视角脱敏与角色权限矩阵

### 1. 读取视角（`GET`）
- `has_subject_capability=False`（`view == "partner_default"`）：
  - 剥离 NPC 知晓记录中未公开的 `subject_private_impression`；
  - 剥离 `style_migration` 中所有 `shared == False` 的未公开风格草稿、隐藏总条数 `total_count` 以及单条 `private_note`。
- `has_subject_capability=True`（`view == "subject"`）：
  - 完整返回主体私人街坊感言与个人风格笔记（已 `revoked` 的街坊分享引用在双视角下均扣留正文摘要）。

### 2. 写入权限（`POST` `actor_side`）
- `actor_side == "partner"` **仅允许以下 4 个动作**（调用其余动作一律返回 `403 FORBIDDEN_ACTOR_SIDE`）：
  - `restock_pantry`
  - `set_partner_presence`（且不允许传入 `subject_foreground_activity`）
  - `propose_contact`
  - `release_contact`
- `actor_side == "subject"` 允许全部 14 个标准动作：
  - 厨房域：`restock_pantry`、`cook_or_brew`、`reheat_dish`、`consume_dish`、`discard_dish`
  - 共在与接触域：`set_partner_presence`、`set_hand_slot`、`set_subject_contact_posture`、`propose_contact`、`respond_contact`、`release_contact`
  - 街坊与风格域：`record_npc_knowledge`、`revoke_npc_shared_ref`、`record_style_migration`

---

## 四、状态码与错误码速查表

| HTTP 状态码 | `reason_code` | 触发条件 |
|---|---|---|
| `200` | `None` | 查询成功、动作首次应用（`status: "applied"`）或同键幂等重放（`status: "already_applied"`） |
| `400` | `INVALID_FIELDS` | 非对象请求体、未知外籍字段、空/空白键名、伪造身份/权限键 |
| `400` | `INVALID_UNITS` / `INVALID_TIMESTAMP` / `INVALID_*` | 非正整数份数、浮点数、布尔冒充整数或枚举值非法 |
| `403` | `FORBIDDEN_ACTOR_SIDE` | `partner` 越权调用主体专属动作，或未知 `actor_side` |
| `403` | `PRIVATE_MATERIAL_ISOLATED` | 试图向街坊 NPC 传入 `diary`、`DRAFT`、伴侣私聊、`36B` 成年亲密或私密材料 |
| `404` | `DISH_NOT_FOUND` / `NPC_NOT_FOUND` / `SHARED_REF_NOT_FOUND` / `STYLE_ENTRY_NOT_FOUND` | 指定餐食、街坊、分享引用或风格条目不存在 |
| `409` | `INSUFFICIENT_PANTRY_STOCK` | 下厨/煮茶所需食材库存不足（原子拒绝，零负库存） |
| `409` | `DISH_EMPTY` / `DISH_NEEDS_REHEAT` / `DISH_SPOILED` | 份数已吃空归零、隔夜餐食未热透、或已过保质变质 |
| `409` | `NOT_WITHIN_REACH` | `absent` 或 `same_room_apart` 时发起近身身体接触 |
| `409` | `HAND_SLOT_OCCUPIED` | 手槽正拿物件导致接触手数不足，或正处于牵手/拥抱接触时强行占手拿物 |
| `409` | `NPC_CLOSED_OR_AWAY` | 在街坊店铺非营业时段发起当面互动或展示作品 |
| `409` | `REVISION_CONFLICT` / `OPERATION_KEY_CONFLICT` | CAS 版本不匹配，或同一 `operation_key` 挂载了不同请求载荷 |
| `503` | `STATE_SYMLINK_REFUSED` / `STATE_CORRUPTED` | 状态路径存在符号链接或 JSON 损坏，失败关闭拒绝猜测 |
