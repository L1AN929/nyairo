# v3.0 第 23/26/27/28 章 · 有限视野、递材料与听说边界

本文记录**已经落地**的状态文件、HTTP 面、Hermes 接线与上下文桥接。三个板块都不调用任何产品模型
（`llm_calls == 0`），读面 100% 零磁盘写入。

## 1. 状态与落盘

- 状态文件：`<day_root>/phone/v03_encounters.json`
- `schema_version`：`life.v03.encounters.v1`
- 写入：同一目录临时文件 + `os.replace` 原子替换；读路径不建目录、不写临时文件、不触碰 mtime。
- `operation_key`：同一 key、同一负载重复提交返回 `already_applied=True`（`DUPLICATE`）；
  同一 key 换负载返回 `409 OPERATION_KEY_CONFLICT`。账本最多保留最近 256 条。

三个板块均有界，超限时优先淘汰最早/已撤回条目：

| 板块 | 上限 | 用途 |
| --- | --- | --- |
| `encounters` | 24 | 同一份材料的三次改观 |
| `shared_materials` | 16 | 伴侣递来的文字/主题材料 |
| `npc_errands_and_hearsay` | 20 | 街坊手头事与二手信息来源 |

## 2. 三次遇见（Ch.23 / Ch.28）

`encounters[]` 字段：`encounter_id / target_ref / target_title_cn / stage / seen_pages /
total_pages / unseen_pages_withheld / current_impression_cn / prior_impressions /
active_question_cn / noticed_detail_cn / partial_disagreement_kept / tried_in_work_ref /
shared / updated_at`。

阶段严格递进：

1. `FIRST_GLANCE`：`record_first_glance` 只接受显式给出的 `seen_pages`（必须落在
   `1..total_pages`）。没看过的页不会因为整本存在而被标记已读，`unseen_pages_withheld`
   恒为 `true`。越界页码直接 `400 INVALID_FIELDS`，不落盘。
2. `QUESTION_DRIVEN_REVISIT`：`revisit_with_question` 带着具体问题重看，合并
   `newly_seen_pages`（去重排序），记录 `active_question_cn` 与 `noticed_detail_cn`；
   旧印象推入 `prior_impressions`（最多保留最近 4 条）。
3. `NUANCED_RECOGNITION`：`record_nuanced_recognition` 写入具体评价并**强制保留局部不同意**
   （`partial_disagreement_kept`）。`tried_in_work_ref` 未提供时保持 `null`，系统绝不从
   上下文里推断「已经试过」。

## 3. 递一页材料（Ch.27 §四.2）

`shared_materials[]` 字段：`material_id / title_cn / excerpt_cn / topic_tag / page_count /
status / opened_pages / reaction_cn / used_in_artifact_id / dropped_at / updated_at`。

| 动作 | 谁可做 | 语义 |
| --- | --- | --- |
| `drop_shared_material` | 伴侣（仅 CSRF） | `RECEIVED_UNOPENED`，`opened_pages=[]`；没有红点、没有未读债务 |
| `open_or_use_shared_material` (`open_partial`) | 主体 | `OPENED_PARTIAL`，记录真实翻过的页与一句反应 |
| `open_or_use_shared_material` (`use_in_work`) | 主体 | `USED_IN_WORK`，必须带 `used_in_artifact_id` |
| `withdraw_shared_material` | 伴侣（仅 CSRF） | 即刻清空 `excerpt_cn` / `reaction_cn` / `used_in_artifact_id`，状态 `WITHDRAWN` |

撤回后的材料：再次打开返回 `409 MATERIAL_WITHDRAWN`；任何投影、摘要或上下文桥接都不再输出
标题与旧正文（`summarize_encounters_for_context` 直接跳过 `WITHDRAWN` 行）。

## 4. 店主手头事与听说（Ch.26）

`npc_errands_and_hearsay[]` 字段：`entry_id / npc_id / npc_label_cn / place_id / kind /
summary_cn / hearsay_source_cn / verified_in_person / verified_place_note /
subject_help_status / updated_at`。

- `kind=hearsay` 必须给出非空 `hearsay_source_cn`（是谁听说的）；默认
  `verified_in_person=false`。
- 只有显式 `verified_in_person=true` **且**给出 `verified_place_note`（在哪里亲眼看见）才记为
  亲眼所见；缺其一时 `400 INVALID_FIELDS`。
- `subject_help_status`：`none / suggested / adopted / moved_aside`，用 `suggested` 与
  `adopted` 区分顺手帮一下和真实委托。

## 5. HTTP 面

### `GET /api/phone/v03/encounters`

- 纯读，100% 零写入；复用宿主/来源闸门、`UNKNOWN_QUERY` 与 `_desk_subject_view`。
- 无凭据 = 伴侣视角：过滤所有 `shared is False` 的遇见，**不输出隐藏计数**；坏凭据 403，
  不降级成空成功。
- `schema = nyairo.v03.encounters.v1`；`perspective` 为 `partner` / `subject`；`llm_calls = 0`。

### `POST /api/phone/v03/encounters`

- `_guard_mutation`（CSRF 必需）+ `UNKNOWN_QUERY` + `_read_v02_crossmodal_json_body`
  （`uid/actor/subject/owner/...` 等身份键 409 `CLIENT_IDENTITY_REFUSED`）。
- 视图层再拒绝 `author_side` / `by`（409 `CLIENT_IDENTITY_REFUSED`）。
- `PARTNER_ALLOWED_ACTIONS = {drop_shared_material, withdraw_shared_material}`：只需 CSRF；
  其余动作缺少有效 `X-Phone-Capability` 时 `403 FORBIDDEN_ACTOR_SIDE`。
- 未知字段 `400 INVALID_FIELDS`；动作名未知同样拒绝。

## 6. Hermes 与上下文接线

- `chiyo_phone`：
  - 只读动作 `v03_encounters`；加入 `_SUBJECT_VIEW_READ_ACTIONS`、`PRIVATE_ARTIFACT_ACTIONS`。
  - 写面 `phone_life_update(target_surface="v03_encounters")`；加入 `LIFE_UPDATE_SURFACES`、
    `LIFE_UPDATE_PRIVATE_SURFACES`、幂等键与主体 capability 白名单。
  - `drop_shared_material` / `withdraw_shared_material` 由 `_PARTNER_ONLY_SUBACTIONS` 拦截，
    模型侧返回 `PARTNER_IMPERSONATION_REFUSED`。
- `plugins/chiyo/__init__.py`：`_PHONE_AND_WORLD_GUIDANCE` 加入 `v03_encounters`，并把导语
  冗词收紧；总长 1175 字符（`<= 1200`），既有动作名全部保留。
- `lived_context_bridge`：
  - 并发只读 `/api/phone/v03/encounters`，或读磁盘 `phone/v03_encounters.json` 后调用
    `summarize_encounters_for_context`。
  - 输出单行 `<= 140` 字：有限视野页数、带问题重看/局部异议、伴侣递来的材料（只温和提起，
    不催读）、店主手边事与「听说自谁」。
  - `shared is False` 按门控剥离；`WITHDRAWN` 材料绝不出词；总上下文仍受
    `MAX_LIVED_CONTEXT_CHARS == 1800` 约束。

## 7. 验收

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest \
  tests.test_v08_v03_epistemic_encounters \
  tests.test_hermes_phone_brain_wiring \
  tests.test_hermes_hot_reconnect_and_surfaces -v
```

结果：44 tests OK（含真实 loopback HTTP：零写 GET、伴侣过滤、主体/伴侣权限、身份拒绝、
撤回后重新打开 409；`test_hermes_hot_reconnect_and_surfaces` 现在覆盖 32/32 个
`phone_life_update` 写面）。

## 8. 明确未做

- 不把材料正文写进聊天记录、日记或 `/state`。
- 不为「未读」制造提醒、红点、连续催促或还款式债务。
- 不自动替主体判断「已经试过」；`tried_in_work_ref` 只能来自真实作品出处。
- 不把听说升级成亲见；也不把顺手帮忙记成委托。
