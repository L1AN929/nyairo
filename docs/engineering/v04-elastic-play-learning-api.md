# v0.4 弹性日常方向、共同试验与十分钟玩心接口契约（`/api/phone/v04/elastic-play`）

对应总体设计第 45–53 章，Schema 为 `phone.v04.elastic_play.v1`，状态落盘于 `<day_root>/phone/v04_elastic_play.json`（`0600` 原子替换写入），全程 `llm_calls == 0`。

## 1. 读接口（`GET /api/phone/v04/elastic-play`）
- 100% 零磁盘写入：文件或目录缺失时返回内存默认投影（`revision == 0`），不创建任何文件或目录。
- 视角隔离：
  - 伴侣视角（无 `X-Phone-Capability`）：`prepared_waiting` 阶段的小惊喜完全隐去；`offered` 阶段仅返回 `surprise_id`、`invite_hint` 与 `status: "offered"`，绝不剧透 `title` / `content`；`private_two_phrasings` 置空且标记 `private_two_phrasings_withheld: true`。
  - 主体视角（有效 `X-Phone-Capability`）：完整可见自身 `prepared_waiting` 惊喜与 `private_two_phrasings` 草稿。

## 2. 写接口（`POST /api/phone/v04/elastic-play`）
- 主体专属动作（伴侣调用返回 `403 FORBIDDEN_ACTOR_SIDE`）：
  - `set_day_direction`：温和记录 1–2 个可选方向（`pre_scheduled: false`），支持中途自然打断（`interrupt_reason`）与凭真实回执 `receipt_ref` 记录完成项（缺失 `receipt_ref` 返回 `409 MISSING_COMPLETION_RECEIPT`）。
  - `save_two_phrasings`：私下并排保存两种表达草稿。
  - `prepare_or_offer_surprise`：`stage="prepare"` 存入 `prepared_waiting`；`stage="offer"` 向伴侣发出无压力拆看邀请。
- 伴侣专属动作（主体调用返回 `403 FORBIDDEN_ACTOR_SIDE`）：
  - `respond_prepared_surprise`：`decision="reveal"` 拆开展示；`decision="later"` 温和暂存不扣分。
- 双方共享动作：
  - `append_shared_notebook`：无截止日期共享小本子；若传入旧 `expected_head_revision` 产生并发追加，自动标记 `has_concurrent_branch: true` 并同时保留两侧分支字句。
  - `share_reading_notice`：共读页码与摘句留痕。
  - `propose_or_respond_experiment`：局部改稿小试验（`propose` / `record_take` / `resolve`），不设输赢计分。
  - `teach_scoped_preference`：限定在 `partner_explanation` / `work_revision` / `world_claim_reported` 范围内生效，`applies_to_private_writing: false`。
  - `request_or_answer_practice`：分角色出题与作答练习，不打数字分数。
  - `play_temporary_game`：十分钟临时规则小游戏（`odd_naming` / `word_chain` / `object_logbook`），随时 `stop` 回到普通聊天，可选 `save_keepsake` 留存小纪念。
