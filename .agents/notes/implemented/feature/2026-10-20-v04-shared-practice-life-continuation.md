# Agent Note: v0.4 共同实践的 Life 内容半边 —— 一条来话＝一次真步骤，只属于真实当前 joint

Status: implemented

## Problem

上一阶段（social）只做到「来话只观察、邀请一次 Decision、共处有结束、之后自己续作」：joint 时间
里**没有内容** —— 没有共享材料、没有用户与主体的真实贡献、没有可再打开的作品。本阶段要求「有内容
的相处」，且必须走正常入口（assembly / resident / runner_tick），不能靠 operator helper 或手调状态冒充。
落地过程中暴露两个更细的缺口：

1. 「有 open 的作品」被当成了「可以继续说话」的凭据：只要 A 里存在一条 OPEN 的作品（包括手机侧
   刚开出来的**候选**），一条普通来话就会触发一次内容请求并写她的那一半；
2. 装配层的 A 服务工厂按**名字**找绑定，而真实一天实例把主体写成 `NYA02_SUBJECT` /
   `CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT`，于是每个真实 profile 上内容半边都静默降级成
   `SHARED_PRACTICE_NOT_CONFIGURED` —— 等于没有。

## Decision

1. **传输扩展只加一个字段**：邀请 payload 的 `material_refs`（结构化 artifact 引用，≤6、去重、
   `^[A-Za-z][A-Za-z0-9_\-]{1,31}:\S{1,200}$`）。普通来话/结束事件带它一律
   `CONTACT_FIELD_NOT_ALLOWED_FOR_KIND`，坏形状 `CONTACT_MATERIAL_REFS_REFUSED`。形状规则单一来源
   `shared_practice_life.bound_material_refs`。
2. **开作品 = 受邀且 Decision 真正 ACCEPT 之后**（`life_social_contacts._invitation` →
   `_practice_open`）：作品的开场文本以 `basis={"event_ref": <这条邀请事件>}` 交给 A（A 用本实例
   自己的读面核对），operation key 就是该邀请事件，重试是重放而不是分叉。joint_state 起始
   `CANDIDATE`：**打开不是接纳**。没有材料就不开会话（`SHARED_PRACTICE_MATERIAL_REFUSED`，具名）。
3. **把「哪一件作品属于这一场 joint」写在 joint 自己身上**：`FiniteCoPresence.bind_practice`
   在受接受的 co-presence 会话记录里写 `practice_session_ref` / `practice_bound_at` /
   `practice_invitation_event_ref`。只有 `state=OPEN` 且邀请对得上才写，否则按名字拒绝
   （`PRACTICE_BIND_REQUIRES_OPEN_SESSION` / `PRACTICE_BIND_FOR_ANOTHER_SESSION`）。
4. **内容门（`SocialContactPort._practice_gate`）**：一条来话只有同时满足下面全部才算「继续这一场
   joint」，否则照样只观察（`llm_calls=0`，门的原因写进 item 的 `practice_gate`，不写正文）：
   co-presence 会话现在 `OPEN`；它的 `joint_activity_ref` 在本实例 owner 里真的是 `ACTIVE`；
   存在 open 的作品；会话里绑定的 `practice_session_ref` 正好就是那件作品；作品自身 `OPEN`。
   于是：手机开出的候选（无 joint / 未绑定）、`PAUSED` 的作品（A 的 pause 有效）、已 `ENDED` 的作品、
   已 `COMPLETED` 的 joint、绑定指向别的会话、重启后读不到绑定 —— 一律**不 content**。
5. **一条来话＝一次真内容步骤**（`SharedPracticeLife.step_message`）：用户自己的话用 `actor=user`
   的 A 服务写（最多 PENDING）→ 回读 head → **一次**严格 schema 内容请求 → 她的最终可见回应用
   `actor=life` 写、`basis={"event_ref": <该来话事件>}`（`LifeSourceVerifier.get_event` →
   `LocalContactInbox.get_event` 真核验）→ 可选真实贡献 → 可选新作品全文（CAS head 每次回读）。
   回应永远 `commitment_state=NONE`：**答复不是答应**。
6. **严格 schema，不放宽不重试**：只允许 `reply/contribution/work_text`；多余键 `INVALID_SCHEMA`、
   未闭合对象 `MODEL_TRUNCATED`、空 reply/非字符串拒绝；一次失败只花一次请求，事件记 `UNKNOWN` +
   具名原因（不进 `pending()`，回程是 `inbox.operation(<key>)`）。
7. **单本账 + 发送前 admission**：内容请求走 `life_request_budget.BudgetedModelClient`（kind=`content`），
   账本就是本实例 `cycle.budget.requests`（AG-1 用的同一对象），并在传输发送边界前过
   `pause_admission`；预算耗尽**不发**（`llm_calls=0`，`REASON_BUDGET_REFUSED`）。
8. **装配层把 owner 的事实交给 A 的名字**（`autonomous_life_assembly.resolve_shared_practice`）：
   用本实例 Supply port 已经用的 `subject / artifact_grant / supply_socket / workspace` 填
   `NYAIRO_LIFE_SUBJECT / NYAIRO_ARTIFACT_GRANT / NYAIRO_LIFE_SUPPLY_SOCKET /
   NYAIRO_LIFE_WORKSPACE_ID` 再交给 A 的工厂；`resolve_local_contacts` 把 `admission` 与 `requests`
   透传下去，内容 responder 与其它 writer 共用同一 transport pause guard 与同一本账。
   缺配置仍返回具名 `UNAVAILABLE`，绝不给半工作替身。
9. **内容进 context，不是 OPEN 标签**：`cognition_block()` 的 `shared_practice` 与
   `agency_cognition_adapter._bounded_situation` 都带真实材料标题/摘录、作品正文、最近贡献（有界），
   并显式 `not_authority{activity,relationship,commitment,memory}=False`。

## Verification（脚本级，真实 exit）

* `python3 -m unittest tests.test_v04_shared_practice_life_seam -q` → **29 tests OK, exit 0**
  （传输 5 + 严格 schema 6 + 真 A 服务内容链 9 + 端口 4 + 内容门 5）。
* `python3 -m unittest tests.test_v04_shared_practice_life_chain -q`（WSL，真 POSIX 链）
  → **3 tests OK, exit 0**：真 Supply/World/runtime/assembly/`runner_tick`，
  读页 1 → 材料邀请 → ACCEPT/handoff → 3 条真实用户文本各 1 次请求、作品逐版（seq 1..9、
  6 贡献/3 回应、owner 字节里是第三版全文）→ 显式 end（joint COMPLETED、原私人资格 OPEN、
  作品 ENDED）→ 同 tick 的正常资格事件 fresh RESUME 同一 Activity 读页 2 → 关闭再同实例重开读回
  作品/归属/seq/事件结果/原断点，`pending=0`、0 内容请求、无重放。
* `python3 -m unittest tests.test_v04_social_life_core -q` → 14 OK；`tests.test_life_v1_integration`
  的 8 个红与本包无关（AR-0 closeout 路径 `ALREADY_DONE`，见结果文档）。

## Alternatives considered

* **让每条来话都跑一次 AG-1 Decision，再从 Decision 里取回应文本**：AG-1 的输出是候选选择，没有
  可见正文；把回应塞进 Decision 会让「决定」和「说话」混成一层，还要改 AG-1 schema。否决：内容
  步骤用自己的严格 schema，Decision 仍然只管「要不要开始/继续」。
* **把用户那份贡献也让 Life 的 life 角色写**：A 的角色分离就是为了防止生命侧替用户说话。否决：
  保留两个 A 服务实例（life/user），Life 只转发 transport 里真实到达的那段文字。
* **「有 open 的作品就继续说话」**：最省事的做法，也是本阶段修掉的缺陷 —— 它让手机侧刚开出的
  候选、被 pause 的作品、joint 已经结束的作品，全都变成「她在继续跟你做东西」。否决：门必须看
  当前 joint 与会话自己的绑定，并且在 owner 里真读到 joint `ACTIVE`。
* **把绑定只放在 Life 进程的内存里**：重启就丢，重启后会重新变成「任意 open 作品都能触发」。
  否决：绑定写在 co-presence 会话记录里（那本来就是这场 joint 自己的存档）。
* **内容请求绕开账本/admission（直接 client.complete）**：会造出没人付账、也不受暂停约束的请求。
  否决：与 AG-1 共用 `cycle.budget.requests`，发送边界前过 `pause_admission`。
* **失败后自动重发/重试一次**：会重复扣账并可能重复写入她的回应。否决：UNKNOWN + operation key 对账。

## Consequences

* 收益：joint 时间里第一次有真的材料、真的双方贡献、真的作品版本；结束之后作品与历史仍可读；
  重开实例不重复写、也不再「凭一条 open 作品就说话」；预算、admission 与失败都留痕，父级能看到
  每步花了多少次请求、停在哪一段。
* 代价：一段共同实践需要一次额外模型请求（content）与多次 owner 读写（每次写后回读 head），比
  「只观察」贵；内容 schema 变严，模型答歪就是失败而不是“差不多”；内容门多几次 owner 读
  （co-presence 会话 + joint Activity + 作品列表），来话 tick 的 owner 读量略增。
* 未闭合：父级 12 attempts/900 秒的真实模型验证还没开始（本阶段 0 次真实联网）；链里的模型是脚本
  原始 JSON，不是自然模型行为。`tests.test_life_v1_integration` 的 8 个红来自 AR-0 closeout 路径
  （`ALREADY_DONE`），不在 B 的写集内，未修。
