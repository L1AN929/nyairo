# 居家收官：重逢锚点 / 夜茶菜谱 / 待定小约定

手机端 `P` 的一层薄投影。所有 owner 事实（World、Supply、阅读、分享、产权/基座、
今日安排）仍由原 owner 决定；本面只新增三份实例内轻量 JSON，不建第二内容库、权限、
日程或 Memory，也不起 timer。

三份状态都在 `<root>/phone/`：

| 文件 | 内容 | 上限 |
| --- | --- | --- |
| `reunion_anchor.json` | 一次显式 `capture` 的安全快照（可无） | 1 张锚点 |
| `home_recipes.json` | 双方显式共同菜谱卡 | 12 张 |
| `tentative_plans.json` | 显式暂定项 | 6 条 |

所有 `POST` 先验 CSRF；`author_side=her` / `proposer=her` 再验本次 serve 的本地
capability。`actor` / `author_side` / `proposer` 都是自报，不是身份。只需 CSRF 的动作
如果显式带了错误的 capability，仍返回 403，不降级。每个动作按字段是否存在做严格白名单：
不接受的字段即便值是 `[]` / `null` / 空串，也返回 `400 UNKNOWN_FIELD:<name>` 且零写。
必填字段为空/空白返回 400。所有校验在保存前完成；原子写沿既有本地小状态原语。

## 重逢锚点 `GET/POST /api/phone/home/reunion-anchor`

`POST` 唯一动作 `capture`，请求体只接受 `{"action":"capture"}`。客户端不能提交时间、
快照、计数、作品引用或分享正文；快照由服务端现读真实 owner。仅需 CSRF。

锚点只存**可披露**的安全事实：

- 抓取时的本地时间、时钟来源与偏移；
- World 可信位置区域（`status/place_id/area_id/home`，读不到具名 unknown）；
- 读物稳定身份 `artifact_id + source_revision` 与**已保存**停点页码（不存正文/页注）；
- 已 `LOCAL_DELIVERED` 分享的稳定 id 集合（不存未发分享标题/正文）；
- 已购物件实例的稳定 id、安全名称与真实摆放摘要（购买不冒充送达/摆放）。

重复 `capture` 若披露状态没变，比较忽略抓取时间，返回 `already_applied`，不改
revision / updated_at。源读不到标 `unavailable/unknown` 并具名，不当 0 参与新增计算；
局部缺源时锚点保存可用块，`complete=false`，不伪造成完整。

`GET` 纯读：无锚 `has_anchor=false`。有锚时用真实 id 集合比较：

- 阅读：仅当 `artifact_id + source_revision` 相同且两边停点已知时给 `page_delta`；
  材料换身份/重置 → `READING_IDENTITY_CHANGED`，页码未知 → `READING_POSITION_UNKNOWN`；
- 已投递：`new_share_ids` 来自真实 `LOCAL_DELIVERED` 集合，DRAFT 不算；
- 物件：`new_item_ids` 按稳定 id；位置两边都未知时不猜搬动（`moved_unknown`）。

`at_home` 来自 World；时钟来源与偏移具名；不把墙钟差冒充离开时长。
`no_absence_debt=true`、不自动催促、不算关系分、不调用模型、不改 inbox 已读。

## 夜茶菜谱 `GET/POST /api/phone/home/recipes`

`POST` 唯一动作 `upsert`。字段：`action`、可选 `recipe_id`、`title`（必填非空，≤40）、
`memo`（必填非空，≤240）、`author_side=her|partner`（必填）。

- 新建/更新语义：`recipe_id` 可省；省略时由 `author_side + title` 派生稳定 id，
  同一 create 重放不会多卡；给出 `recipe_id` 时按 id 更新/创建。
- 既有卡作者不可改；双方都不得覆盖对方卡（有效 her capability 也不能把 partner 卡改 her，
  反之亦然）→ `409 RECIPE_AUTHOR_MISMATCH`。
- 编辑才递增该卡 `revision` 与整体 `revision`；同标题同 memo 重放 `already_applied`。
- 最多 12 卡；第 13 张 `409 RECIPE_LIMIT_REACHED`。没有删除动作。

`author_side=her` 需 CSRF + capability；`author_side=partner` 只需 CSRF，但显式带了
错误的 capability 仍 403。该声明只选择所需验证策略，不授予 her 身份。
卡是双方显式共同菜谱，不自动导入她的私密笔记；`partner_contributed=true` 且不进入
Memory / Belief / 经历。

`GET` 返回卡与只读参考区：来源是 `world_phone_shopping.inventory` 的真实购买/消费回执
（最多 6 条有界）。购买与消费分开显示；`not_evidence_of_cooking=true`、
`consumption_claimed=false`、`matched_by_title=false`、`per_card_link=null`——同名不等于
吃过某道菜。参考区不是做菜/吃过证据。

## 待定小约定 `GET/POST /api/phone/home/tentative-plans`

`POST` 动作 `propose` / `reschedule` / `drop`。字段白名单按动作：

- `propose`：`action`、可选 `plan_id`、`title`（必填非空 ≤60）、可选 `window_note`
  （≤40，省略或空串表示暂不写时间）、`proposer=her|partner`（必填）；
- `reschedule`：`action`、`plan_id`（必填）、`window_note`（必填非空 ≤40）；
- `drop`：`action`、`plan_id`（必填）。

状态只有 `TENTATIVE` / `RESCHEDULED` / `DROPPED`，没有通用 status 写口，也没有
`ACCEPTED`/核心义务。`proposer` 始终不变。

- `propose` 同一意图重放返回同 id、`already_applied`，不挤满 6 条；新想法同 id 不同内容
  → `409 PLAN_ALREADY_EXISTS`（改期请用 `reschedule`）；最多 6 条。
- `reschedule` / `drop` 重复不递增 revision；`DROPPED` 不会被 `reschedule` 默默复活
  （新想法应新 `propose`）。
- 权限：`proposer=her` 或改/放 her 的项需 CSRF + capability；`proposer=partner` 与改/放
  partner 的项只需 CSRF（错误 cap 仍 403）。没有 cap 却自报 her → 403；
  `reschedule`/`drop` 不接受 `proposer`/`title` 等字段，夹带即 400。
- 每项 `auto_hijack_activity=false`、`no_obligation=true`；绝不导入核心安排、
  `plan_today`、`FutureItems`、canonical Activity 或 Memory，不伪造到期自动执行。

`GET` 把①正式今日安排的安全摘要与②本地暂定项分列。默认只给安全数量/状态/日期，不返回
私人 title、`source_ref`、`resume_point`、memo 或作品引用；带本人 capability 时沿既有授权
附 `plan_titles` / `plans_detail`。安排源不可读时 `status=unknown` 并具名，不假装为空。
只读：不改主账本、不改 inbox 已读。

## 验证与边界

`tests/test_home_hearth_view.py` 覆盖纯投影与真实 Supply/World/阅读/分享/产权 loopback：
无锚/重复捕获、真实翻页/新投递/新购买 delta、未发分享扣留、双方菜谱写与跨作者拒绝、
12 卡上限、参考区不冒消费、暂定双方权限与 6 条上限、核心 arrangements/canonical 不变、
每动作额外字段 400 零写、错 cap/CSRF 负控、三 GET 重复状态 bytes 与 owner head 零写。

未覆盖：生成本 fixture 的 World 内容没有声明该小点面为可消费，故没有跑正式 `CONSUME`；
参考区如实显示真实购买、`consumption.status=not_consumed`，不扩 World 协议、不手造回执。
没有 resident 长 tick 观察窗口，因此不承诺 World 后台永不写。
