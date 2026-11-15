# 房间外六项日常接口说明（`outing_places_slice`）

对应 `15-产品记忆与自主机制完整设计.md §2.8`「房间外」全部六项日常细节，提供三条纯本地 loopback HTTP 读面与显式写面：

1. `GET / POST /api/phone/outing/bench-bookstore`：熟悉河边长椅重访留痕 + 会换橱窗且影响阅读灵感的书店。
2. `GET / POST /api/phone/outing/routes-market`：散步路线与随手拍相册引用 + 虚拟预算节日集市。
3. `GET / POST /api/phone/outing/gallery-blueprint`：可重访个人作品小展廊 + 双人共同画图并定义功能的新地点草图。

## 1. 存储与硬边界

* 三份状态文件均以原子 `0600` JSON 存放在 `<root>/phone/` 下：
  * `outing_bench_bookstore.json`
  * `outing_routes_market.json`
  * `outing_gallery_blueprint.json`
* **纯读零写**：所有 `GET` 绝不创建 `<root>/phone/` 或任何文件，绝不推进 World / Life / Supply / 分享状态。损坏 JSON 或符号链接一律具名报错并失败关闭，写面拒绝覆盖。
* **鉴权分离**：
  * `GET` 无 capability 返回 `partner_default`；带有效 `X-Phone-Capability` 返回 `subject`；伪造或错实例 capability 一律 `403`。任何未知 query 一律 `400 UNKNOWN_QUERY`。
  * `POST` 一律先验 `X-CSRF-Token`；本人动作（记录长椅、改长椅公开性、收藏橱窗书单、保存/移除散步路线、记集市心愿、布置/撤下展品、`author_side="her"` 的新地点草图）额外必须携带有效 `X-Phone-Capability`。伴侣留言动作（`set_partner_window_note` / `clear_partner_window_note` / `set_partner_market_tip` / `clear_partner_market_tip` / `author_side="partner"` 草图）仅需 `X-CSRF-Token`，但若显式附带坏 capability 仍立即 `403`。
  * 所有 `POST` 按精确 `action` 校验允许字段集合，跨动作附带多余字段（哪怕传空串 `""`）一律 `400 UNKNOWN_FIELD:<name>`。

## 2. 接口契约摘要

### 2.1 `GET / POST /api/phone/outing/bench-bookstore`

* **读面要点**：
  * `bench`：从真实 World 提取非 `home` 户外地点与水岸/公园/高台候选长椅点；`partner_default` 仅返回 `shared=True` 的重访记录且剥离 `operation_key`；`no_streak_score=True`（不刷打卡连击分）。
  * `bookstore_window`：按本地日期 `YYYY-MM-DD` 从 9 本固定书目确定性轮换 3 个橱窗展位（`slot_index` `0..2`），核对真实 World 是否存在书店地点；`reading_completed=False`、`model_calls=False`。
* **写面动作**：
  * `log_bench_visit`（本人）：`{action, place_id, operation_key, note?, shared?}`，核对真实非 `home` World 地点，按 `operation_key` 幂等重放。
  * `set_bench_visibility`（本人）：`{action, visit_id, shared}`。
  * `bookmark_window_book`（本人）：`{action, slot_index, note?, shared?}`。
  * `set_partner_window_note` / `clear_partner_window_note`（伴侣）：`{action, slot_index, note}` / `{action}`。

### 2.2 `GET / POST /api/phone/outing/routes-market`

* **读面要点**：
  * `routes`：最多 12 条散步路线（每条 `1..6` 个不重复非 `home` 真实 World 地点，可选绑定真实 `PhoneAlbum` 图片）。`partner_default` 只返回 `shared=True` 路线与 `photo_status`（`available` / `withheld` / `none`），绝不暴露底层 `album_artifact_id`。
  * `festival_market`：只读 `world_property_economy` 真实虚拟币余额（未创世则如实报 `GENESIS_NOT_INITIALIZED` 与 `balance_jpy=None`），按日期轮换 3 个节令摊位；`virtual_currency_only=True`、`does_not_debit_wallet=True`、`real_money_involved=False`、`changes_relationship=False`。
* **写面动作**：
  * `save_walk_route` / `remove_walk_route`（本人）。
  * `note_market_wish`（本人）：`{action, stall_code, memo, budget_cap_jpy?, shared?}`。
  * `set_partner_market_tip` / `clear_partner_market_tip`（伴侣）。

### 2.3 `GET / POST /api/phone/outing/gallery-blueprint`

* **读面要点**：
  * `gallery`：最多 8 件来自真实 `PrivateProjectService` 精确 `(artifact_id, version_id)` 的展品。源已 `ARCHIVED` 则双视角均 `ARCHIVED_SOURCE` 扣留；`partner_default` 逐条复核该精确版本是否存在真实 `LOCAL_DELIVERED` 分享，未投递则连同 `wall_caption` 一并 `VERSION_NOT_DELIVERED` 扣留。
  * `place_blueprints`：双人分署名（`her` / `partner`）最多 6 张活跃新地点草图卡，`blueprint_only=True`、`does_not_mutate_world_topology=True`。
* **写面动作**：
  * `curate_exhibit` / `remove_exhibit`（本人）。
  * `upsert_blueprint` / `archive_blueprint`（双人按 `author_side` 各自署名，跨作者修改或归档一律 `409 AUTHOR_MISMATCH`）。
