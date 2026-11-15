# v0.4 街区院子·手作守恒·书店公共生活 API 契约（`phone.v04.neighborhood.v1`）

本文件定义 `v0.4` 批次 A–F 的单一确定性后端引擎与手机本地视图门面契约，对标《CHIYO 0.4 · 世界建设规划》第 `38`–`43`、`45`、`46` 章及第 `47` 章“做减法”原则。

---

## 1. 模块位置与存储铁律

- **领域引擎**：`components/world/scripts/v04_neighborhood_living.py`
- **HTTP 视图与动作闸门**：`components/phone/v04_neighborhood_view.py`
- **单一确定性状态文件**：`<day_root>/world/v04_neighborhood_state.json`
  1. **纯 GET 100% 零磁盘写入**：`read_v04_neighborhood_view`（以及状态文件尚不存在时的 `advance_v04_neighborhood_processes`）在任何目录上均不执行 `mkdir`、不创建临时文件、不改动任何 `mtime`。
  2. **原子安全落盘**：写操作统一通过同目录 `0600` 临时文件 + `fsync` + `os.replace` 原子写入；发现符号链接立即返回 `503 STATE_SYMLINK_REFUSED`；遇到损坏 JSON 或非法 schema 严格失败关闭（`503 STATE_CORRUPTED`），绝不静默重置覆盖。
  3. **零模型调用与开源角色中性**：全链路纯 Python 标准库确定性推进（`llm_calls == 0`，无 `random` 抽样），角色身份零写入读取 `phone/character_pack_state.json`，默认回退为中性 `"TA"`（`locked_to_chiyo: false`）。

---

## 2. Python 标准接口签名

### 2.1 `read_v04_neighborhood_view`

```python
V04_NEIGHBORHOOD_SCHEMA = "phone.v04.neighborhood.v1"

def read_v04_neighborhood_view(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]
```

- 返回 `(200, envelope)`，顶层含 `ok: True`、`status: "ok"`、`schema: "phone.v04.neighborhood.v1"`、`read_only: True`、`perspective: "her_private" | "partner_default"`、`revision: int`、`observed_at: str`、`llm_calls: 0` 与 `data` 字典（所有 `data` 顶层键同时镜像在 `envelope` 顶层）：
  - 领域核心 7 键：`environment`、`places`、`journey`、`drying_items`、`potted_plant`、`craft_bench`、`public_life`
  - 桌面端与 Hermes 桥直绑键：`revision`、`identity`、`weather_and_ground`、`journey`、`courtyard`（内含 `plants`、`drying_rack`）、`public_notices`、`roadblocks`、`shared_shelf`、`crafts`、`bookstore`、`place_booklets`

### 2.2 `mutate_v04_neighborhood_view`

```python
def mutate_v04_neighborhood_view(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str,
    now_iso: str | None = None,
) -> tuple[int, dict[str, Any]]
```

- **字段白名单与防伪造栅栏**：严格校验 `ACTION_ALLOWED_FIELDS`；任何多余未知字段、空键、身份保留字（`uid` / `authority` / `owner` / `subject` / `price` / `balance` / `source` / `actor` / `artifact_id` 等）或好感度/情绪分数键（`mood_delta` / `affection_score` 等）一律返回 `400 INVALID_FIELDS`。如需关联作品引用，统一使用 `source_artifact_id` 或 `work_ref`。
- **角色权限隔离**：`actor_side` 支持 `"partner"` 与 `"subject"`（兼容 `"her"` 归一为 `"subject"`），越权调用一律返回 `403 FORBIDDEN_ACTOR_SIDE`。
- **幂等与并发控制**：全部 12 个动作均支持可选 `operation_key`、`intent_fingerprint` 与 `expected_revision`；同 `operation_key` 同语义负载重放返回 `(200, {"status": "already_applied", "already_applied": True, ...})`；同键异参返回 `409 OPERATION_KEY_CONFLICT`；版本失配返回 `409 REVISION_CONFLICT`。

### 2.3 `advance_v04_neighborhood_processes`

```python
def advance_v04_neighborhood_processes(
    day_root: Path | str,
    *,
    now_iso: str | None = None,
) -> dict[str, Any]
```

- 统一推进已存在的晾晒分段结算、耐受盆栽水分/生长、在途移动到达或封路目视边界、书店预留到期及公共告示过期。
- 若 `<day_root>/world/v04_neighborhood_state.json` 尚不存在，零写盘安静返回 `{"ok": True, "advanced": False, "llm_calls": 0}`。

---

## 3. 动作白名单与权限矩阵

| 动作名 (`action`) | `partner` | `subject` / `her` | 核心职责与关键约束 |
|---|---|---|---|
| `set_local_weather` | 允许 | 允许 | 切换 `clear`/`drizzle`/`rain` 与 `airflow`（`weak`/`medium`/`strong`），先结算旧区间再切新段（`segment_count += 1`） |
| `hang_or_collect_cloth` | 允许 | 允许 | 在 `home_rack`、`porch_hook`、`yard_line`、`hand_bag` 间挂收或弄湿布巾；湿度 `<= 5.0` 进入 `sleeping_dry=True` |
| `water_or_move_plant` | 允许 | 允许 | 浇水、在 `home`/`porch`/`yard` 挪动盆栽或切换 `public_care_enabled`；`never_dies_offline=True` |
| `post_public_notice` | 允许 | 允许 | 张贴带有效期的公共告示（可选绑定 `related_route_id` 与 `roadblock_claim`），未读前仅露标题摘要 `"有一张新纸"` |
| `set_roadblock` | 允许 | 允许 | 更新真实路段路障，**绝不直接写入** `journey.discovered_roadblocks`（严守非全知边界） |
| `shared_shelf_loan` | 允许 | 允许 | 共享架 `paper_ruler` / `craft_clips` 的 `borrow`、`return`、`request_extension`（零好感奖惩） |
| `start_journey` | 403 | 允许 | 支持 `open_shortcut`（4 分钟露天）与 `eaves_walkway`（7 分钟有檐）分叉选路及 `carry_item_ids` 父链随行 |
| `advance_or_turnback_journey` | 403 | 允许 | 推进在途移动或执行中途 `turn_back`（创建折返意图并保留已走时长，绝不瞬移回滚） |
| `read_public_notice` | 403 | 允许 | 走近阅读告示全文，标记 `read_by_subject=True` 并同步未过期告示中的封路线索 |
| `craft_paper_or_box` | 403 | 允许 | `cut_card` / `restock_paper` 整数材料守恒（不足返 `409 INSUFFICIENT_MATERIAL`）；`inscribe_card` 锁定 `inscribed_revision=1` 与 `draft_text` 隔离；`transcribe_card` 递增版本；`card_organizer_box` 多步半成品保留与 `repair_patch` |
| `bookstore_service` | 403 | 允许 | `browse_book`（仅封面/目录/摘句）、`reserve_book`（到期恢复可借）、`borrow_book`/`return_book`（闭店或归还箱进入 `return_box_pending`）、`submit_display_card`（仅公开确定版本，隔离私人草稿） |
| `update_place_booklet` | 403 | 允许 | 仅 `knowledge_status == "visited"` 地点可记实地安静度（否则 `409 PLACE_NOT_VISITED_YET`）；支持 `pause_booklet` 零扣分暂停，默认私密 |

---

## 4. 视角隐私与认知边界摘要

1. **`partner_default` 视角脱敏**：
   - 未公开私人卡片（`shared_or_displayed=False`）：`inscribed_text` 脱敏为 `None` 且 `withheld=True`。
   - 全部卡片的 `draft_text` 始终脱敏为 `None`（即便卡片已送书店陈列，私人草稿亦绝不外泄）。
   - 未共享地点小册（`shared_with_partner=False`）：`entries` 清空、`title`/`purpose` 为 `None` 且 `withheld=True`。
2. **`her_private` 视角非全知边界**：
   - 后台通过 `set_roadblock` 设置的路障，在主体未走到该路段入口且未通过 `read_public_notice` 读到相关告示前，不会出现在 `journey.discovered_roadblocks` 或主体视角的 `roadblocks` 列表中。
   - 公共告示在调用 `read_public_notice` 之前，`body` 为 `None`（`passing_summary: "有一张新纸"`）。
