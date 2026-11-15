# 局部看法修订、物件容纳与书桌餐桌共在 API 契约（`stance_containment_slice`）

本文件定义本地服务端纯确定性、零模型调用、零随机数的后端接口与状态契约：

- 视图模块：`components/phone/stance_containment_view.py`
- Schema：`phone.stance.containment.v1`
- 状态文件：`<root>/phone/stance_containment.json`（`0600` 原子替换落盘，拒绝符号链接）
- 对标原稿：v3.0 第 30 篇（有限注意与看法修订）、第 31 篇（物件用途与空间习惯）、第 12/13/14 篇（双人书桌 B7 协议、餐桌厨房消耗生态、附着式共在状态机与消耗品整数守恒）及《总体审阅总结与实施补充》§五 P0 #1、P1 #6、P1 #7。

---

## 1. 核心架构不变量与边界铁律

1. **GET 100% 零写入与损坏失败关闭（Fail-Closed）**
   - 当 `<root>/phone/stance_containment.json` 不存在时，`read_stance_containment(root, *, now=None, subject_view=False)` 严禁创建目录或写入任何字节，直接投影默认状态（`revision: 0`）。
   - 若状态文件或父目录为符号链接、JSON 损坏或违反守恒约束，只读接口返回 `state_status="degraded"` 与具名 `state_reason_code`；写接口直接抛出 `409 StanceContainmentConflictError`，绝不静默覆盖。
2. **开源去角色死绑**
   - 动态通过 `sensory_character_view.read_character_pack(root, subject_view=True)`（或回退只读角色包状态投影）获取当前角色包的 `pack_id`、`display_name` 与 `pronouns`（未配置时默认中性预设 `"TA"`，`locked_to_chiyo: false`）。
3. **严格字段白名单与防伪造身份防火墙**
   - 按 `action` 校验精确允许字段集合：任何多余字段返回 `400 UNKNOWN_FIELD:<name>`；显式传入 `null` 或纯空白字符串返回 `400`；伪造身份或内部状态字段（如 `subject_id`、`actor_ref`、`after_state`、`superseded_by`、`impression_stale`、`affection_score`）一律 `400 UNKNOWN_FIELD:<name>` 拦截。
   - 本人认知/双手/准备餐点动作要求 `subject_view=True`，伴侣视角调用一律抛出 `403 SUBJECT_CAPABILITY_REQUIRED`。

---

## 2. 三大内聚子域契约

### 2.1 子域一：局部看法与观点修订谱系（`stance_revisions`，第 30 篇 + 审阅 P1 #6）

- **闭合 `facet` 枚举**：仅接受 `"appearance"`、`"content"`、`"method_fit"`（其余一律 `400 STANCE_FACET_INVALID`）。同来源不同 `facet`（如封面 `appearance` 与第 2 页 `method_fit`）天然并存，互不覆盖。
- **精确比较锚点**：每条看法绑定 `(scope_ref, source_revision, page_or_anchor, facet)`。
- **单一写路径与双向谱系**：
  - `propose_stance`：新建一条局部看法，`shared_with_partner` 默认 `false`（公开前伴侣视角完全剥离该条目正文、私密备注及未公开计数）。
  - `fix_stance_typo`：仅修错别字，强制校验 `expected_version`（CAS 冲突抛 `409 STANCE_VERSION_CONFLICT`），递增 `text_version`，**不改变** `stance_id`、`replaces_stance_id` 或 `superseded_by`。
  - `revise_stance`：观点真正改变时**绝不原地覆盖旧看法正文**，而是在同一状态提交内生成新看法（写入 `replaces_stance_id`、`lineage_depth + 1`），同时将旧看法标为 `status="SUPERSEDED"` 并写入 `superseded_by=<new_stance_id>`。对同一旧看法并发二次修订抛 `409 STANCE_ALREADY_SUPERSEDED`。
  - `mark_source_updated`：材料换版为 `new_source_revision` 后，该来源下旧版本活跃看法自动转为 `SOURCE_REVISION_STALE`（移出 `current_stances` 进入 `stale_stances`，绝不冒充对新版材料的看法）。
  - `revoke_stance_source`：来源撤权或删除后，对应条目立即转为 `SOURCE_WITHDRAWN`，清退落盘正文与 `private_note`，停止向 `current_stances`、`lineage_history` 及伴侣视图输出；调用 `rebuild_stance_index` 或重启重载**绝不复活**。

### 2.2 子域二：物件容纳、真实部分完成与观察印象分离（`object_containment`，第 31 篇 + 审阅 P1 #7）

- **单层容纳与派生用途**：内置桌面/书架容器（`pen_cup` 容量 2、`ceramic_mug` 容量 2、`desk_tray` 容量 3、`tote_bag` 容量 4）与可放入小物件（`fountain_pen`、`gel_pen`、`sketch_pencil`、`brass_bookmark`、`memo_clip`、`reading_glasses`、`house_keys`）及双手槽 `left_hand` / `right_hand`。杯具空置时只读派生 `derived_use="empty_drinkable"`，插笔后派生 `"holding_items"`（`can_pour_drink_now=false`），取出后自动恢复，不设永久变身魔法指令；非空容器禁止移除（`409 CONTAINER_NOT_EMPTY`）；禁止容器套容器（`409 NESTED_CONTAINER_FORBIDDEN`）。
- **复合动作真实部分完成（绝不假回滚）**：
  - `pick_and_put_in_container` 先拿起可达小物件进入空闲手槽，再尝试放入目标容器。
  - 若拿起成功但目标容器已满（`CONTAINER_FULL`）、不在当前可达位置（`CONTAINER_NOT_REACHABLE`）或拒绝该物件类别（`CONTAINER_CLASS_REJECTED`），**绝不假装把物件回滚回原桌面**，而是真实落盘保存在手槽内（`status="partial_completed"`、`partial_completed=true`、`rolled_back=false`、`held_in_hand="right_hand"|"left_hand"`）。
- **移动父容器连带子物 & 观察印象分离**：
  - 容器移动时，内部所有子物件的物理有效位置 `effective_place_id` 随父容器同步改变。
  - 当伴侣在主体未目击/不在场时调用 `partner_move_container` 挪走容器，容器及内部子物件的物理位置立即更新，但主体的位置印象 `subject_last_seen_place` 仍停留在旧位置（`impression_stale=true`），主体认知提示绝不后台全知透视新位置；直到主体调用 `observe_current_room` 亲自观察所在房间后，`subject_last_seen_place` 才刷新为真实位置且 `impression_stale` 归为 `false`。
  - 伴侣视角严格剥离所有物件/容器上的 `private_impression_note` 与 `subject_cognitive_cues`。

### 2.3 子域三：双人书桌附着式共在与餐桌份数守恒（`shared_desk_table`，第 12/13/14 篇 + 审阅 P0 #1）

- **单前台附着式共在括号（`copresence`）**：
  - 四态闭合枚举：`"off"`、`"quiet_parallel"`（安静各忙各的并行陪伴）、`"focused_do_not_disturb"`（专注勿扰）、`"break_chat"`（歇口气闲聊）。
  - 支持双人分立提议与应答（`propose_copresence` -> 对方 `respond_copresence`，禁止同一方自提自签 `409 SELF_COUNTERSIGN_FORBIDDEN`），也支持直接切换 `switch_copresence_mode` 与随时单方无债退出 `exit_copresence`。
  - 共在会话仅作为附着括号绑定 `attached_to_activity_id = primary_activity.activity_id`，不开第二前台；若主活动处于不可打断 `ATOMIC` 步骤，切入 `break_chat` 返回 `409 PRIMARY_ACTIVITY_ATOMIC_BOUNDARY`；退出共在绝不终止或清空主体原本的手头作品活动（`hijacks_primary_activity: false`、`zero_exit_debt: true`）。
- **厨房餐桌整数份数守恒与共食（`table_dishes`）**：
  - 支持主体准备茶点餐食（`prepare_table_dish`）与伴侣带来点心饮品（`partner_bring_snack`），严格使用整数份数 `initial_portions == portions_remaining + consumed_by_subject + consumed_by_partner + discarded_portions`。
  - 温热档位按 `now - prepared_at` 纯函数确定性派生：`"hot"`（`<20` 分钟）-> `"warm"`（`20..60` 分钟）-> `"cooled"`（`>=60` 分钟），只读查询不落盘推进。
  - 双方调用 `take_portion` 真实扣减 `portions_remaining`；扣至 `0` 时状态变为 `"EMPTY"`（`is_empty_plate: true`），空盘留在桌面占格直至显式 `clear_table_dish`（未吃光直接收走须显式传 `discard_remaining=true`，剩余部分记入丢弃而非摄入）；空盘继续取用抛 `409 DISH_EMPTY_NO_PORTIONS_LEFT`，绝不无限刷吃，且全程 `no_hidden_affection_score: true`。

---

## 3. 公开 Python 接口与异常映射

### 3.1 导出函数
- `read_stance_containment(root: Path, *, now: str | datetime | None = None, subject_view: bool = False) -> dict[str, Any]`
- `apply_stance_containment_action(root: Path, payload: Mapping[str, Any], *, now: str | datetime | None = None, subject_view: bool = False) -> dict[str, Any]`

### 3.2 结构化异常
| 异常类 | HTTP 状态码 | 触发场景 |
|---|---|---|
| `StanceContainmentValidationError` | `400` | 非法 JSON 对象、缺失/未知 `action`、未知字段 `UNKNOWN_FIELD:*`、空串/空值字段、非法枚举或范围 |
| `StanceContainmentPermissionError` | `403` | `subject_view=False` 调用主体专属动作或伪称 `actor="subject"` / `consumer="subject"` |
| `StanceContainmentNotFoundError` | `404` | `stance_id`、`container_id`、`item_id`、`dish_id` 或待应答共在提议不存在 |
| `StanceContainmentConflictError` | `409` | CAS 版本冲突、旧看法已被替代、来源已撤权/换版、手槽已占、不可达、非空容器移除、自提自签、空盘再取、幂等键冲突 |

### 3.3 支持的 `action` 列表（共 27 个）
- **看法修订链（7 个）**：`propose_stance`、`fix_stance_typo`、`revise_stance`、`set_stance_sharing`、`mark_source_updated`、`revoke_stance_source`、`rebuild_stance_index`
- **物件容纳与位置印象链（11 个）**：`pick_up_item`、`take_from_container`、`put_in_container`、`place_on_surface`、`pick_and_put_in_container`、`subject_move_container`、`partner_move_container`、`set_subject_location`、`observe_current_room`、`set_item_impression_note`、`remove_container`
- **书桌共在与餐桌共食链（9 个）**：`set_primary_activity`、`propose_copresence`、`respond_copresence`、`switch_copresence_mode`、`exit_copresence`、`prepare_table_dish`、`partner_bring_snack`、`take_portion`、`clear_table_dish`
