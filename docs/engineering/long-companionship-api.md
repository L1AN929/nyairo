# 长程陪伴六项日常接口说明（`long_companionship_slice`）

对应 `15-产品记忆与自主机制完整设计.md §2.8` 长程陪伴最后六项日常细节与 `16-生活生态与长程研究完整设计.md §10 G` 回忆空间「双层灯光」，提供三条纯本地 loopback HTTP 读面与两条显式写面：

1. `GET / POST /api/phone/longterm/first-room-habits`：第一天房间照与今日房间新旧对比 + 薄荷/室内盆栽观察连到手账插画 + 可修订共同习惯册。
2. `GET / POST /api/phone/longterm/gifts-lantern`：保留草稿到成品心意轨迹的共同礼物 + 一年后重画/重写谱系与回忆空间「双层灯光」（`?layer=then_only|with_later_context`）。
3. `GET /api/phone/longterm/resume-hub`：纯零写一键续接上次项目/书页/话题 + 已关闭归档项目无催促总览 + 备份恢复状态只读总览。

## 1. 存储与硬边界

* 两份状态文件均以原子 `0600` JSON 存放在 `<root>/phone/` 下（`resume-hub` 为纯零写现读聚合，不设状态文件）：
  * `longterm_room_habits.json`
  * `longterm_gifts_lantern.json`
* **纯读零写**：所有 `GET` 绝不创建 `<root>/phone/` 或任何文件，绝不推进 World / Life / Supply / 分享状态。损坏 JSON、非法结构或符号链接（文件或父目录）一律具名失败关闭，写面拒绝覆盖（`409`）。
* **鉴权与字段栅栏**：
  * 三条 `GET` 无 capability 返回 `partner_default`；携带有效 `X-Phone-Capability` 返回 `subject`；伪造或错实例 capability 一律 `403`。
  * `first-room-habits` 与 `resume-hub` 拒绝任何 query（`400 UNKNOWN_QUERY`）；`gifts-lantern` 仅接受可选单次 `?layer=then_only`（省略时默认）或 `?layer=with_later_context`，未知键、重复键或非法值一律 `400`。
  * 所有 `POST` 一律先验 `X-CSRF-Token`（缺失或错误 `403`）及 `_optional_capability_refusal()`（显式附带坏 capability 立即 `403`）。本人动作（`capture_first_room`、`link_plant_sketch`、`remove_plant_sketch`、`record_later_reflection` 以及 `author_side == "her"` 的习惯/礼物动作）额外必须携带有效 `X-Phone-Capability`；伴侣动作（`author_side == "partner"` 的习惯/礼物）仅需 `X-CSRF-Token`。
  * 所有 `POST` 按精确 `action` 校验允许字段集合，跨动作多余字段（哪怕传空串 `""`）一律 `400 UNKNOWN_FIELD:<name>`。

## 2. 接口契约摘要

### 2.1 `GET / POST /api/phone/longterm/first-room-habits`

* **读面要点**：
  * `first_room`：对比封存的首日基线与实时房间事实（`furniture_delta`、`item_delta`、`new_item_names`、`new_plant_names`、`reading_page_delta`、`delivered_share_delta`），`no_absence_guilt=True`。`partner_default` 剥离底层 `album_artifact_id` 与私密 `note`，仅返回 `photo_status`（`available` / `withheld` / `none`）。
  * `plant_sketch_links`：最多 8 条家中真实盆栽与活跃 `project` / `PhoneAlbum` 图片的观察连结。`partner_default` 仅返回 `shared=True` 条目，且当 `target_kind == "album"` 或未投递 `project` 时一律剥离底层 `target_artifact_id`。
  * `shared_habits`：最多 12 条活跃 / 24 条总存共同习惯，保留最近 5 次修订历史（`version`、`previous_text`、`revised_reason`、`revised_at`）；`no_hard_constraint=True`、`does_not_hijack_decision=True`。
* **写面动作**：
  * `capture_first_room`（本人）：`{action, album_artifact_id?, note?}`。首日基线一次性封存不可变；若已 `captured=True`，任何后续请求返回 `200` `status="already_applied"`（`already_applied=True`），绝不覆盖第一天记忆。
  * `link_plant_sketch` / `remove_plant_sketch`（本人）：核对真实家中盆栽（否则 `409 PLANT_NOT_IN_HOME`）与活跃手账项目/相册图片（否则 `409 SKETCH_TARGET_NOT_FOUND`）。
  * `upsert_habit` / `retire_habit`（双人按 `author_side` 各自署名，跨作者修改或停用一律 `409 AUTHOR_MISMATCH`）。

### 2.2 `GET / POST /api/phone/longterm/gifts-lantern`

* **读面要点**：
  * `shared_gifts`：最多 12 件礼物（`"draft"` / `"wrapped"` -> `"gifted"`）。**核心隐私边界**：`partner_default` 中任何 `author_side == "her"` 且 `stage != "gifted"` 的礼物完全隐去，且不暴露隐藏草稿数量；一旦 `send_gift` 送出变为 `"gifted"`，同时公开 `draft_seed_note`（草稿初念）与 `finished_note` + `gifted_at`（若挂载 `(artifact_id, version_id)`，仅当该精确版本已有 `LOCAL_DELIVERED` 分享时展示 `linked_work_excerpt`，否则 `linked_work_status="withheld"`）。
  * `memory_lantern`：扫描 `PrivateProjectService` 真实 `derived` 作品来源谱系与最多 12 条 `later_reflections`。
    * `layer="then_only"`（默认）：`later_context_included=False`，仅展示当年固定的 `then_snapshot`，`later_remakes=[]`、`later_reflection=None`，过去版本绝不预知未来。
    * `layer="with_later_context"`：`later_context_included=True`，照亮 `later_remakes` 与后来回望（`partner_default` 仅显示 `shared=True` 回望）。`partner_default` 对 `then_snapshot` 与每条 `later_remake` 分别独立核对 `ARCHIVED_SOURCE` 与精确版本 `LOCAL_DELIVERED`（`VERSION_NOT_DELIVERED`）。
* **写面动作**：
  * `upsert_gift`（`"draft"` / `"wrapped"`）与 `send_gift`（转为 `"gifted"`，送出后不可回退或重送 -> `409 GIFT_ALREADY_SENT`；跨作者 -> `409 AUTHOR_MISMATCH`）。
  * `record_later_reflection`（本人）：`{action, source_artifact_id, source_version_id, reflection_note, shared?}`。

### 2.3 `GET /api/phone/longterm/resume-hub`

* **纯零写只读聚合**（无 `POST`）：
  * `project_cue`：`subject` 返回最新 `ACTIVE` 项目与最新已投递项目分享；`partner_default` 仅返回 `has_active_project` 布尔值与已 `LOCAL_DELIVERED` 项目分享摘要，绝不泄露未分享项目标题。
  * `reading_cue`：当前阅读书签页码与总页数（章节标题提示仅 `subject` 可见）。
  * `topic_cue`：最近一条已本地送达分享的安全话题锚点。
  * `closed_projects`：统计已正式关闭（`ARCHIVED`）项目数量，`closed_cleanly=True`、`no_nagging=True`（`partner_default` 绝不暴露归档标题或 ID）。
  * `recovery_overview`：纯只读调用 `runtime_recovery_review`（未绑定 `one-day` 时诚实返回 `UNBOUND`），汇总 `{status, forget_barrier_status, read_only: True}`。
