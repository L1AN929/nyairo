# v0.8 Wave 3 · 街道 / 告示 / 小册 / 书店展陈的日常桥接与 Resident 接缝

本文只说明已落地的事实：这些机会从哪来、谁执行、写到哪里、哪些边界没有动。

## 1. 数据来源（只读、零写入）

`components/life/scripts/v02_v03_daily_loop_bridge.py` 的
`collect_v02_v03_loop_candidates()` 继续是 **100% 零写入** 的采集器。本轮在
`_collect_v04_neighborhood_candidates()` 里补齐 Ch.46 批次 B / E / F：

| 批次 | source_ref | action_family | v04 命令 |
| --- | --- | --- | --- |
| B 途中推进 | `v04_journey_step:<route_id>` | `advance_neighborhood_journey` | `advance_or_turnback_journey`（`advance` / `resume` / `turn_back`，只看 journey 自己宣告的状态） |
| E 公共告示 | `v04_public_notice:<notice_id>` | `read_public_notice` | `read_public_notice` |
| F1 地点小册 | `v04_place_booklet:<place_id>` | `update_place_booklet` | `update_place_booklet`（`record_entry`） |
| F2 书店展陈 | `v04_bookstore_display:<card_id>` | `submit_display_card` | `bookstore_service`（`service=submit_display_card`） |

- 告示只读 `public_notices` 里 **未读且未过期** 的条目；候选与主体视角都不携带正文
  （`body_withheld_until_read=true`）。
- 小册只收录 `knowledge_status == "visited"` 且还没写进 `entries` 的地点；小册 `paused`
  时不再生成。写入参数 `shared_with_partner=false`，伴侣视角继续 `withheld=true`。
- 展陈只在 `is_poi_open("bookstore", now)` 为真时生成；卡面必须 `inscribed_revision >= 1`
  且 `displayed_at_bookstore` 不为真。
- 生病门控沿用既有 Wave 1：`creative_intensive`（小册）在 `creative_block` 时被
  `ILLNESS_CREATIVE_BLOCK` 拦下；`requires_outing`（途中推进 / 展陈）在
  `blocks_strenuous_outing` 时被 `ILLNESS_BLOCKS_STRENUOUS_OUTING` 拦下；告示阅读两者都
  不触发。

`has_v02_v03_active_state()` 仍是零写入检查，但只要真的存在
`world/v04_neighborhood_state.json`（常规文件、非软链）也返回 `True`，让只有院子和街道、
还没有 `phone/*.json` 的实例进入桥接。

## 2. 结算

所有新增动作都走既有 `settle_v02_v03_round_consequences()` →
`apply_v04_neighborhood_command(day_root, {...}, actor_side="subject")`，没有新的写入路径：

- 身份锁：`actor_side` 必须是 `subject / her / life_resident`；
- 回执门控：`effect_state == "APPLIED"` 且带 `receipt_ref` 才落账；
- 幂等：`operation_key` 账本防重放，重放返回 `already_applied`。

## 3. Cycle → Resident 真接缝

- `AutonomousLifeCycle`：当提交决策选中的是桥接候选映射出来的
  `fitem:v02v03-...`（且描述符带 `v04_action`）时：
  - `_evaluate_once_round` 把它算作一条真实材料（`materials()` 里加入桥接候选计数），
    不再因为“没有作品 / 没有到期便签”而 `NO_MATERIAL`；
  - 采纳形状与该步都由决策绑定：`_v02_v03_adoption_spec()` 给出
    `V02_V03_DAILY_NEIGHBORHOOD_STEP` 的有界 Activity，`_v02_v03_step_spec()`
    给出 `INTERNAL_CHECKPOINT_STEP`（`action_kind=None`，不过 AR-0 围栏）；
  - 分类器 `_classify_v02_v03_daily_step()` 在同一有界步骤里通过桥接结算该 v0.4 命令，
    只有结算返回 applied / already_applied 才上报 `ACTED`，然后向 LR-5 提交完成事实；
    被拒绝时是命名的 `STEP_REFUSED`，Activity 不完成。
  - 因此不会再触发 `DECISION_NAMED_NO_WORK` / `OUTCOME_NO_STEP_AVAILABLE`，也不会落回任意
    写作作品。
- `AutonomousLifeResident._run_v02_v03_bridge_hook()`：
  - 只有 `decision ∈ {START, CONTINUE}` **且** `status/status_class == ACTED` 时才结算；
    `WAIT / NO_ACTION / DEFER / HARD_REJECT / STEP_REFUSED` 一律不动世界；
  - 候选的原始 `v04_*` 引用依次从 `origin_source_ref`、`target_refs`、`source_ref` 解析，
    因此映射成 `fitem:v02v03-...` 后仍能匹配回原始描述符；
  - 结算事件与 cycle 步骤使用同一桥接事件/operation key，本轮第二次结算是
    `already_applied` 重放，真实 v0.4 命令只执行一次。

## 4. 未改动

- `v04_neighborhood_living.py` / `v04_neighborhood_view.py` 的契约未改；本波只复用既有动作。
- 冻结文件 `autonomous_life_assembly.py` / `life_sharing_service.py` /
  `readonly_recall_adapter.py` 零改动。
- 不新增依赖，不新增外部协议。
