# Agent Note: 双人书桌四态木牌、未发短笺与明日便签

Status: implemented

## Problem

设计 15 §2.7/2.8 要的是桌面上能看见的小片：门上的四态木牌、摊开的当前作品/读物、桌角
「欲言又止」的未发短笺、床头的明日便签。现有能力各自都在，但没有一个只读面把它们收在
一起；桌面端若要自己拼，就得分别猜 World 位置、canonical 前台、Body 恢复会话、阅读停点、
短笺状态与 FutureItems 到期日，很容易把「读不到」显示成「她在客厅、随时可聊」，或者把
历史 artifact 全说成摊在桌上。另一端，短笺与便签正文是私人内容，不能因为「桌面聚合」
就绕出既有授权。

## Decision

1. **新增纯投影模块** `components/phone/desk_presence_view.py`，`PhoneService` 只负责取
   既有 owner 的只读事实并调用它；World/Body、canonical Activity、阅读工作台、作品 head、
   `LifeSharingService`、`FutureItems` 都不改契约。
2. **四态按证据分级**：位置可信且 `location.home=false` → `OUT_IN_TOWN`；在家且 Body
   `recovery.state=active` + `mode=sleep_like` → `RESTING`；在家且 canonical 前台
   `ACTIVE` → `FOCUSED_WORK`；在家、恢复会话可读、前台无 ACTIVE → `AT_HOME_OPEN`。
   普通 `rest` 是醒着的休息，不单独算睡着（`resting_basis` 明写）；`PAUSED`/`WAITING`
   不冒专注；位置、canonical 或恢复事实缺一项就不出四态，只给 `unknown/unavailable` +
   具名原因，**不默认在家开放**。
3. **桌面只放当前事实**：当前作品取 **canonical 前台 Activity 自己的 target 绑定**
   （`origin_ref`/`checkpoint_ref`/`target_ref` 的单值优先，其次唯一一个 artifact
   `subject_refs`；版本引用丢弃，多个对象不猜首项），再用 owner 的 `read_project` 读它
   的 head/版本；读物、私人事项文档、非文本 artifact 与短笺草稿不算「桌上的作品」。
   实例启动时的 `work_artifact_id` 只作为 `bound_work` 返回并标 `is_current=false`，绝不
   在前台实际是另一件新作品时把它当成当前作品。读物取现有 `PhoneReading.material` 的
   页码/总页/疑问数与停点。没有把 `list_projects` 的最新一件当成「摊在桌上」。
   `partner_presence` 无真实依据恒为 `NOT_RECORDED`。
4. **两读一写**：`GET /api/phone/desk/presence`、`GET /api/phone/desk/notes` 纯读；
   `POST /api/phone/desk/tomorrow` 先 CSRF、再本次 serve 的既有本地 capability，然后只
   转发到 `FutureItems.propose/cancel/reschedule`，写后重新读回同一对象。没有新 store、
   timer、模型调用或自动改期/完成/发送。短笺写继续用既有 `/shares` prepare 与 actions。
5. **隐私默认扣留**：默认视角只给木牌、安全元数据、留存计数与 `body_withheld`；短笺正文、
   短笺标题、`defer_condition`、明日便签标题一律不返回。只有本次 serve 的 capability
   匹配时才返回自己视角的有界明文；错误/过期凭据直接 403。没有现成的「伴侣可读未发正文」
   授权机制，因此 `partner_preview_available=false`，并具名
   `NO_EXPLICIT_PREVIEW_GRANT`；已 `LOCAL_DELIVERED` 只在原 inbox 按既有引用呈现。
6. **明日按业务本地日**：读实例 `fastday_clock.json` 的逻辑时刻，取下一本地日；`due_at`
   换算到同一偏移再比日期，不用 `now+24h`，也不混 host 日期。时钟缺失时退回系统时钟并
   在答案里具名。

## Alternatives considered

- **把最新一件作品当成「当前作品」**。最强理由：桌面确实常放最近改过的东西，读起来最像
  生活。否决：这是从历史 artifact 猜「现在摊在桌上」，会把她没在看的旧稿也摆出来。
- **一直只显示启动绑定的 `work_artifact_id`**。最强理由：这是实例上真实、稳定的一个字段，
  实现最短。否决：她后来可能开了另一件新作品，前台 Activity 已有自己的 `origin_ref`；
  继续显示启动作品就是把旧作品当成「正在做」，所以只保留为 `bound_work` 并标非当前。
- **普通 `rest` 也算 `RESTING`**。最强理由：人都躺下休息了，门牌写「休息中」很自然。
  否决：需求明确写「睡姿 lying 单独不等睡着」；`rest` 是醒着的恢复，拿它当睡着会让伴侣
  以为不能敲门。只认 owner 自己的 `sleep_like` 会话。
- **为伴侣开放短笺正文，新增 `visibility=public` / 预览 token**。最强理由：桌面端是双人
  场景，偶尔翻到一张未发短笺很有生活感。否决：这需要新的授权与 ACL，本单禁止；现有
  `LOCAL_DELIVERED` 才是唯一「已给出去」的事实，未发正文不能因 UI 可爱就绕出。保持
  private，并把「没有机制」写成具名原因。
- **新建一个 desk 聚合 store / 缓存快照**。最强理由：多次 GET 会重复读 owner，缓存能省
  开销。否决：快照会变成第二个真相源，还会把「刚发生的变化」藏起来；这些都是便宜的只读
  调用，每次现读即可。
- **让明日便签写口自己生成 `operation_key` 或自动按 next-day 改期**。最强理由：客户端更
  省事。否决：`FutureItems` 的幂等由意图键（title+due+refs+origin）决定，额外的 key 只会
  造第二套账；自动改期/完成则违背「不替她决定」。

## Consequences

- 收益：桌面端第一次能只靠两个 GET 渲染木牌、当前作品/读物、留存短笺计数和明日便签；
  未知与缺证据如实显示；短笺/便签正文默认不出门，自己看时才走既有 serve 凭据；明日筛选
  沿业务时钟，跨午夜不漂。
- 代价 / 已知限制：
  - 没有真实伴侣在场检测，也没有伴侣预览授权；`partner_preview_available=false` 是当前
    能力边界，不是临时 bug。
  - 默认视角仍返回作品/读物标题（沿 `/projects` 与 `/reading/material` 的既有政策）；若
    以后要连标题也对伴侣隐藏，需要另立产品决定。
  - `GET /desk/notes` 的自己视角会对每条留存短笺各读一次 detail；条目数受既有
    `MAX_SHARES=100` 约束，不做全库索引。
  - World/Body 若在同一进程后台 tick，读快照后仍可能有 owner 自己的落盘；本面本身不写。
    真实 owner 集成用例不启 resident/整日循环，World gateway 的 tick 是 3600s，窗口内没有
    待排空写；比对集覆盖 canonical、Supply DB、阅读/私人事项 head、短笺列表与
    FutureItems 文件的字节。旧的 FakePort HTTP 用例只覆盖组件层投影，不冒充真实全链。
  - 预览夹具 `scripts/desk_presence_fixture.py` 走同一个正常构造：真实 Supply
    `Services`、`one_day_life.prepare`、`WorldGateway`、真实 `PhoneReading`，并用真实
    LR-2 START 提交一件前台作品 B；不再手写 canonical Activity、World 位置或阅读返回值。
  - 木牌通信可用性沿用 LR-3 的 `compute_communication_availability`；正在睡（`RESTING`）
    时 LR-3 没有前台评估，返回 `null` + `RESTING_BODY_SESSION_NO_LR3_ASSESSMENT`，不把
    IDLE 的 `OPEN` 冒充成「可以叫她」。
- 父级验收（2026-11-11）：仅运行本轮 `tests.test_desk_presence_view` 一次，26 例 / 7.017s / OK。
  其中纯投影与 FakePort 用例是组件层验证；6 条真实 owner HTTP 用例复用既有 Supply/World/阅读与
  LR-2 写口，Activity 的 Decision/Adoption admission reader 是显式夹具，不冒完整自然决策链。
  本地预览实读到 FOCUSED_WORK、前台 B 的真实 head（不同于启动绑定 A）、书签 2/6 页与 1 条 unclear；
  默认短笺正文/标题和明日标题扣留，伪 actor 不提权、错 capability 为预期 403。
  有效凭据读写、明日项重放/取消、前台切到阅读不回退 A、重复 GET 状态不变由本轮真实 HTTP 用例验证。
  两冻结锚点 SHA256 与施工前基线相同，产品调用 0，未运行历史套件或十阶段 demo，未改桌面端或 commit/push。
  父级预览的新根 `/tmp/nyairo-desk-presence-6135-1794367357680596866` 保留，预览服务验收后停止。
