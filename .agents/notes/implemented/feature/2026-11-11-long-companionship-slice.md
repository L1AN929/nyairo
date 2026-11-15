# 2026-11-11 `long_companionship_slice`（0.2「长程陪伴」最后六项日常 + 回忆双层灯光全补齐）

## 交付内容

* 新增纯投影与原子 `0600` 状态模块 `components/phone/long_companionship_view.py`：
  1. **第一天房间照与今日房间新旧对比**（`longterm_room_habits.json` `first_room`）：首日基线一次性不可变封存（再次调用返回 `already_applied` 绝不覆盖第一天记忆），实时对比家具、拥有物品、室内盆栽、书页与已投递分享增量；`partner_default` 剥离底层 `album_artifact_id` 与私密 `note`，`no_absence_guilt=True`。
  2. **薄荷/室内盆栽观察连到手账插画**（同上 `plant_sketch_links`，上限 8）：核对真实家中盆栽（否则 `409 PLANT_NOT_IN_HOME`）与活跃 `project` / `PhoneAlbum` 图片（否则 `409 SKETCH_TARGET_NOT_FOUND`）；`partner_default` 仅返回 `shared=True` 条目，且对相册目标及未投递项目一律剥离底层 `target_artifact_id`。
  3. **可修订共同习惯册**（同上 `shared_habits`，活跃上限 12 / 总存上限 24）：`her` / `partner` 双署名，跨作者修改或停用一律 `409 AUTHOR_MISMATCH`；更新递增 `revision_count` 并保留最近 5 条修订历史，停用转 `RETIRED` 保留轨迹，`no_hard_constraint=True`、`does_not_hijack_decision=True`。
  4. **保留草稿到成品的共同礼物**（`longterm_gifts_lantern.json` `shared_gifts`，上限 12）：支持 `draft` / `wrapped` -> `gifted` 单向演进（已送出不可回退 `409 GIFT_ALREADY_SENT`）。`partner_default` 下她的 `draft` / `wrapped` 礼物完全隐形且不泄漏草稿数量；`send_gift` 后同时公开 `draft_seed_note` 与 `finished_note` + `gifted_at`，挂载作品按精确版本 `LOCAL_DELIVERED` 校验摘要可见性。
  5. **一年后重画/重写新旧谱系 & 回忆空间「双层灯光」**（同上 `memory_lantern`）：扫描 `PrivateProjectService` 真实 `derived` 作品来源谱系与最多 12 条 `later_reflections`。默认 `?layer=then_only` 仅照亮当年 `then_snapshot`（`later_context_included=False`，过去版本绝不预知未来）；`?layer=with_later_context` 照亮 `later_remakes` 与回望，并在 `partner_default` 对每个源版本与重画版本独立校验 `ARCHIVED_SOURCE` 与精确版本 `LOCAL_DELIVERED`（`VERSION_NOT_DELIVERED`）。
  6. **一键回到上次项目/书页/话题 + 已归档项目无催促总览 + 备份恢复状态只读总览**（`resume-hub`）：纯零写聚合 `project_cue`、`reading_cue`、`topic_cue`、`closed_projects`（`closed_cleanly=True`、`no_nagging=True`，伴侣视角不泄露归档标题/ID）与 `recovery_overview`（现读 `runtime_recovery_review`，`read_only=True`）。
* 接线 `components/phone/service.py` 与 `components/phone/server.py` 三组端点：
  * `GET / POST /api/phone/longterm/first-room-habits`
  * `GET / POST /api/phone/longterm/gifts-lantern`
  * `GET /api/phone/longterm/resume-hub`
* 新增本地合成预览脚本 `scripts/long_companionship_fixture.py`（默认端口 `3860`）、完整单测与真实 loopback HTTP 测试 `tests/test_long_companionship_view.py`（26 项测试）、工程文档 `docs/engineering/long-companionship-api.md`。
