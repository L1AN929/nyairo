# Agent Note: 手机只读时间线 —— 把已有记录合成「她今天有记录的几件事」

Status: implemented

## Problem

她的活动、日计划、World 动作、日记、相册、钱包交易各自都已有 owner 和读口，但没有任何
一个入口能回答「今天**有记录**的这几件事按时间大概是什么样」：

1. `/today` 只列当天开始的 Activity 和当天产生的作品版本，不含日计划、World 动作、
   日记/相册写入或钱包交易；
2. `/today/arrangements` 只回答计划/义务的**排定与执行状态**，不是发生时间线；
3. 让手机页面自己拉五个接口再拼，会把「怎么分日、怎么去重、哪些字段不能给 operator」
   这套规则复制到每个消费者手里；复制几次就会出现第二种事实。

同时有几个不能靠猜的地方：Activity 与日计划写在实例自己的 Life 时钟上，World 的
execution ledger 写 World owner 的时钟，日记/相册/钱包写各自 owner 的真实记录时间；
把这些时间硬排成一条全序，会在压缩日里制造一个看起来精确、实际上跨时钟域的假答案。
日记正文、相册 caption/scene/生成提示词、钱包私有备注也不能因为「反正 record 里有」
就顺手复制进聚合读面。

## Decision

新增**纯读聚合模块** `components/phone/day_timeline.py` 与
`GET /api/phone/today/timeline`，只合并既有记录，不新建 store / JSON / sidecar：

1. **来源与所有者不变**：Activity 复用 `own_day_read.read_today_activities`（与 `/today`
   同一条 +08:00 业务日界和具名失败码）；日计划读 `life_day_plan.DayPlan`，标题从
   `life_future_items` 取，条目标为 `PLAN_ESTABLISHED` 并明说「只表示排进今天」；
   World 动作读 owner 的 `world_execution_ledger`，同一 `execution_id` 的分阶段记录只留
   终态一条，活动中的 travel 记录按 `execution_id` 合并进去而不是双计；日记/相册/钱包
   直接调用既有的 `diary_list` / `album_list` / `wallet` facade。
2. **时域如实分开**：每条带 `time_basis`（`business_clock` / `system_clock` /
   `unknown_time`）。Activity 与计划用实例 Life 时钟；World ledger 在注入了同一业务
   clock 时算 `business_clock`，否则 `system_clock`；日记/相册/钱包算 `system_clock`。
   `ordering.comparable` 只在已知时钟域 ≤ 1 时为 true；否则按域分组、域内按时间排序，
   不跨域伪造全序。`recorded_at` 不冒充 `occurred_at`：只用记录自己的发生/写入时间。
3. **隐私默认骨架**：聚合永不复制日记正文/摘要/派生标题、相册 caption/scene/
   generation_prompt、钱包私有备注，也不带图片字节。operator（无 `X-Phone-Actor`）
   `skeleton_only=true`；`X-Phone-Actor: model` 走同一份骨架加原入口 `detail_ref`，
   正文仍由 `diary_read` / `album_image` 按各自权限回答。来源读不到是具名
   `unavailable`，不变成「她今天什么都没做」；所有来源都读不到才整体 unavailable。
4. **body 只有当前快照**：`body_snapshot` 明确 `time_basis=current_state`、
   `history=NOT_RECORDED`，不插进时间线，也不从当前档位反推过去。
5. **读有界**：每个来源有上限并如实标 `truncated`，顶层最多 300 条；未知 query 在读取前
   拒绝；零写、零模型、零服务启动。

## Alternatives considered

- **新建一张 timeline 表 / sidecar，写入时顺手记一条** —— 否决：那就有了第二个事实源，
  历史回填、删除撤权、owner 各自的时间语义都要再实现一次；还会为了「时间线好看」写日志，
  正好违反「没有记录就是空白」。
- **让手机前端并行拉五个接口自己拼** —— 否决：分日、去重、时域与隐私裁剪会在每个消费者
  手里各写一遍，且 operator/主体视图的差异没有单一位置可审计。
- **把所有时间统一换算到 +08:00 后混排成一条全序** —— 否决：压缩日里业务 clock 与真实
  clock 相差很大，混排会给出一个看似精确的假答案。保留 `time_basis` 与分组排序，
  caller 能看见「这两条不能直接比」。
- **聚合里直接带上日记正文/相册说明（反正来源 record 已有）** —— 否决：这等于新开一个
  默认披露面，绕过日记/相册各自「operator 只读元数据」「caption 默认规则」的既有决定。

## Consequences

- 新增一个只读模块、一条 GET 路由、`chiyo_phone` 的 `day_timeline` 只读动作（并入既有
  私人档案许可）、`nyairo_day.py phone` 的一行摘要，以及 `scripts/phone_day_timeline_demo.py`。
- 既有 `/today`、`/today/arrangements`、日记/相册/钱包各自形状与权限完全不变；聚合入口
  不旁路任何来源的扣留或拒绝。
- 来源缺失（例如还没跑过 resident，没有 `canonical_life_state.json`）时，活动段是具名
  `unavailable`，其它段照常返回；这不等于「今天什么都没发生」。
- World 动作的历史地点只有 record 自己命名时才有（travel 记录的起点/终点）；Activity 与
  ledger 都不带地点，聚合给 `place=null` 与 `NOT_IN_RECORD`，不从当前位置反推。
- 演示 `scripts/phone_day_timeline_demo.py` 用真实 Supply/World/Body 与一次 POSE、一条日记、
  一张合成 1×1 PNG 的既有 album import、一笔既有虚拟商店交易，验证真实记录长出来、重复读
  不增、operator 无正文/说明、同根重开仍在；0 产品模型、0 生图。
- 未覆盖：跨日/跨重启的 Activity 历史（只在实例自己的 canonical 文件里）、World travel
  已完成的历史（facade 只报进行中的那一段）、真正跨进程 crash 后的 ledger 半写恢复。

相关：日计划域 [v0.5 她自己排一天](2026-10-26-life-own-day-plan.md)、日记
[手机日记](2026-10-26-phone-diary.md)、相册
[私人相册导入](2026-10-22-v04-private-album-import.md)（同 Supply 私有件框架）、
`/today` 与 `own_day_read` 的既有实现。
