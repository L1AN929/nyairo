# Agent Note: S08 未来事项的认领、计划分离与到期改期取消（F-093～F-097）

Status: implemented

## Problem

`life_future_items` / `life_day_plan` / AG-1 的 `later_item` / `future_item_actions` /
`plan_today` 已经能写，但没有任何用户可读的入口回答下面几个问题，而它们很容易被压成一个词：

- 这件"以后要做"的事是谁提出的？是她的哪一条**已提交 Decision**？还是只是候选？
- 它现在是未来事项、今天的计划，还是已经采用的 Activity？
- 到期是不是就等于"正在做"？
- `oblig:` 是 operator 给的义务，她能不能顺手取消/改期？
- LR-4 的 eligibility 是不是"自动恢复执行"？
- 有没有 Commitment？放弃 Activity 会不会自动解除承诺？

没有具名答案时，外部读者会把 `fitem:` 当成承诺、把 `plan:` 当成已开始、把 `oblig:`
当成她自选的安排。

## Decision

1. **公开意向键**：`life_future_items.proposal_intent_key()` 成为 `propose()` 与只读面的
   唯一身份公式（title + due + source_refs + origin）。只读面用同一条已提交 Decision 的
   `later_item` proposal 重算这个键来证明归属；算不出来就报
   `NO_DECISION_RECORD_FOUND`，不贴到相邻事项上。
2. **只读审阅面** `components/life/scripts/life_future_organization_review.py`：
   - `future_items` / `day_plan` / `obligations` / `activities` / `agenda` 各自带 `owner`
     与 `actor`，`obligations.writable_by_subject=false`；
   - 取消/完成的事项不再出现在 open 列表，但 `claims` 仍保留它的 Decision 归属；
   - 缺 store 是合法空表；坏 store / 坏 JSON / 坏 schema 是 `UNAVAILABLE` + 原因码；
   - `commitment` 与 `renegotiation` 固定报 `NOT_IMPLEMENTED`
     （`S08_COMMITMENT_OWNER_ABSENT`），不输出任何 `promised` 字段。
   - LR-4 读面在 POSIX 可用；Windows 缺 `fcntl` 时报 `AGENDA_READER_UNAVAILABLE`，
     不伪装成"没有条件"。
3. **用户入口** `scripts/s08_future_review_cli.py`：`--instance` / `--state-root`，
   `--now` 注入业务时刻，`json`/`text` 两种输出；Activity 读复用
   `components.phone.own_day_read.read_today_activities`（同一条 +08:00 日规则）。
4. **可用链演示** `scripts/s08_future_organization_demo.py`：真实 Supply / AG-1 / AG-2 /
   LR-4 / 正式 `evaluate_once`，脚本 Decision 与内容，注入业务时钟：
   - 提交 Decision 写出一条带归属的 `fitem:`；
   - 到期前该轮保持 QUIET（既有 reconsideration 账本，不删记录）；
   - 到期只产生"重新考虑"的 project option，不建 Activity/版本；
   - 同一条 Decision 可同时 `plan_today`，计划行保存 `decision_ref`；
   - 改期按回合时钟解析；取消后旧时刻、重启都不复活；
   - 指向 `oblig:` 的 `future_item_actions` 被域名拒绝，整条 Decision 不提交，义务不变；
   - 有序停服 + 同根重装配读回全部状态，零新 Activity / 版本 / 重放。

## Alternatives considered

- **把 `oblig:` 当承诺**：它已有来源、目标与完成依据，最省事。但它的作者是 operator，
  她只是执行者；这样做会把"别人替我许的愿"变成"我许的愿"，与 F-095 的责任归属相反。
- **把 `fitem:` 当承诺**：延期意向没有对方、没有作出承诺的主体、没有解除语义；
  改个名字就能过演示，但会让"想法"具备约束力。
- **在只读面里按标题模糊匹配 Decision**：会在同名事项之间贴错归属；改为重算身份键，
  匹配不上就如实报无记录。

## Consequences

- F-093/094/096 与 F-097 的事务部分（改期/取消）有可跑的用户入口与真实 owner 链。
- F-095 真实承诺约束、F-097 的重新协商部分仍是 `NOT_IMPLEMENTED`；停点与三条候选路线见
  `docs/engineering/s08-future-organization-options-20261027.md`。
- 到期只进入重新考虑；有前台时 LR-4 的资格不抢占、不自动执行。
- 不是 G1、不是 24h、不是 crash 矩阵；演示的停启是有序同根重装配。
- 相关规则见 [[2026-11-02-v04-autonomous-day-later-and-multi-project]] 与
  [[2026-10-26-life-own-day-plan]]。
