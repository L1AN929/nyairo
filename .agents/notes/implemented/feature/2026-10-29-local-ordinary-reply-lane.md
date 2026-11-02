# Agent Note: 普通来话的本地回复出口 ＋ 在途消息 lane

Status: implemented

## Problem

上一块（`2026-10-29-atomic-ordinary-chat-boundary`）只把 LR-3 的裁决读进了普通消息路径，
真实生产链上还缺三件实事，父级复核时逐条点名：

1. **生产装配没有回应实现。** `resolve_local_contacts()` 构造 `SocialContactPort` 时不传
   responder，树上也没有普通消息的回应实现可交；真实 resident 路径上每条普通消息 =
   `OBSERVED` + `responded=False` + `LOCAL_REPLY_NOT_WIRED`。
2. **回应没有消费者。** 即使注入 responder，正文也只被算成 `chars` 后丢掉；
   `runner_tick()["contacts"]` 不回写、不落盘，本地没有任何入口能取回"她说了什么"。
3. **消息与动作同线程。** `runner_tick` = maintain → contacts → feeds → `run_due(act=True)`，
   同线程串行；真实动作在提交边界阻塞时，消息只能等下一 tick。原子期间"能回话"因此是空的。

用户本窗口直接批准的最小范围：补齐最小本地回复出口与原子步骤在途消息处理，保持单执行
writer、原子步骤不可非法暂停、不外发、0 产品调用。

## Decision

**1. 回复归档在消息 owner 上，不新建 owner。** `life_local_contact.LocalContactInbox` 增加一条
per-event `reply` 记录和两个口：

- `record_reply(event_ref, claim_token=..., text=...)`：只允许 `LOCAL_MESSAGE`；只允许**持有当前
  认领**的消费者写（与 `record_result` 用同一个 claim token 规则）；文本有界（600 字）非空；
  一条消息只有一句——同样的文本重放，不同文本 `CONTACT_REPLY_ALREADY_RECORDED`。
- `reply(ref, include_text=False)`：按 `event_ref` / `operation_key` / `message_ref` 取；默认只给
  `reply_present`/`reply_chars`，正文只在显式 `include_text=True` 时返回。

`_summary`/`_detail` 只多出 `reply_present`/`reply_chars` 与一个不含正文的 `reply` 块，
`summary()` 多一个 `replies_stored` 计数。旧文档没有 `reply` 键 = 没有回复，不迁移、不补写。

**2. 正式 responder 走现成的 provider 与持久预算。** 新模块
`local_message_responder.LocalMessageResponder` 只做一件事：一次 `client.complete(...)`，
纯文本、有界、不开 tool、不写 Memory、不取私文。`autonomous_life_assembly._local_reply_responder`
用与 writer/practice 相同的 profile 解析、`OpenAICompatibleTransport`（同一个 pause send guard）
与 `BudgetedModelClient`（同一本请求账本）构造它；profile 不可用就返回
`LOCAL_REPLY_MODEL_NOT_CONFIGURED`，不拿模板顶替。`resolve_local_contacts(..., local_reply_client=)`
的 `client` 参数是最底层注入点，仅替换 provider 那一层。

**3. `SocialContactPort` 负责把回复落到 owner 上。** `_local_reply` 拿到 responder 的答案后
调 `inbox.record_reply`，只有真的归档成功才算 `responded`（`archived=True`、带 `reply_ref`）；
失败路径（`LOCAL_REPLY_MODEL_FAILED`/`BUDGET_REFUSED`/`PAUSE_REFUSED`/`EMPTY_ANSWER`/`TOO_LONG`/
`NOT_ARCHIVED`）如实具名，绝不回显正文。运输结果保持原语义：只有真归档了才把
`OBSERVED` 的理由写成 `LOCAL_REPLY_RECORDED`，其余仍是 `OBSERVED_ONLY`。

**4. 独立消息 lane 只消费普通消息。** 新模块 `local_message_lane.LocalMessageLane`：一个线程，
只用 `SocialContactPort.tick_ordinary_messages()`（只认领 `LOCAL_MESSAGE`，邀请/结束留在串行
步骤里），claim 用 `consumer="local_message_lane"` 与主 tick 区分，所以两个消费者不可能同时回
同一条；它不跑 cycle、不碰 AG-1/AG-2/AR-0/LR-2/LR-4、不建/不暂停 Activity、不取 lease，
也不跨模型 I/O 持锁（claim 就是 inbox owner 自己的写锁 + token）。`stop()` 只 signal+join，
不杀线程、不清 claim。

**5. resident 拥有这条 lane 的生命周期。** `AutonomousLifeResident.start_message_lane()` /
`stop_message_lane()`：`start()` 里起、`stop()` 里先停（先于 runtime 关闭），
`tick()` 的 journal 记录只多一份计数（`message_lane`），正文与回复永远不进 journal/health/prompt。

**6. 本地入口。** 新增 `scripts/local_reply_cli.py`：`send` / `status` / `reply [--include-text]` /
`list`，全部走 `LocalContactInbox`。只用 `--include-text` 才输出正文；不做 HTTP、不做多受众、不外发。

## Alternatives considered

**（最强）把聊天交给第二个执行器／通用聊天服务（原任务书的 B 方案）。** 立论：一个真正的会话
线程可以有历史、可以排队、可以在任何时刻回话，是最自然的"人在这里"的形态。否决：它就是第二个
Life writer——会碰 Life 写口、会让单执行 writer 与单前台规则失效，而且需要新的会话/历史 owner
与新的并发合同；用户这次批的是"最小本地回复出口 + 在途消息处理"，不是第二执行器。

**把 `SocialContactPort.tick()` 整条搬进线程。** 立论：改动最小，一行线程就能让消息在步骤阻塞
时得到处理。否决：`tick()` 里含邀请路径（一次 AG-1 Decision + AG-2 采纳 + AR-0 动作），把它搬
线程等于让第二个线程写 Life 状态机；所以 lane 只调 `tick_ordinary_messages()`。

**回复只存在内存/直方图里（上一块的做法：只算 `chars`）。** 立论：不新增持久格式，最省事。
否决：这正是被父级点名的"没有消费者"——谁也说不出她回了什么，重启更无从核对。回复必须落在
原消息上、能被按 id 取回。

**用确定性模板当 responder。** 立论：不花预算、永远可用、demo 更稳。否决：模板会冒充她说过的话，
和本仓库"不编她的文字"的既有铁律直接冲突。

## Consequences

- 现在能做到：一条普通本地消息经正式 CLI 投递后，resident 自己的 lane 在**动作阻塞期间**用
  正式装配的 responder 回一句、把这句话存进消息 owner，CLI 按 message id（或 operation_key）
  取回非空正文；同一条消息重投不生成第二句；关掉 lane 换进程仍读回同一句。
- 仍然做不到／未验证：真模型自然采用（Decision 与回复都是脚本替身）、G1、24h、用户真的收到或
  已读（`delivered_to_user=False`，没有外发通道）；"她愿意回什么"仍取决于真实 provider 的答案，
  本单只证明生产路径能跑通。
- lane 的代价：一个常驻轮询线程（默认 0.2s）与一次真实模型请求/条消息（走既有预算账本，
  失败不退，不新增重试合同）。崩溃时 claim 会留在 `CLAIMED` 且可见（与既有语义一致），
  不会被主 tick 抢走重发。
- 证据：`scripts/atomic_chat_boundary_demo.py`（33 项 CHECK，exit 0，含 0 号"无模型配置就叫名字"、
  在途 lane 回复、重复不双生成、预算拒绝、关停后新进程读回、正式 start/stop 与同一本账）、
  `tests/test_atomic_chat_boundary.py`（22 例），周边 `tests/test_v04_local_contact_port.py` +
  `tests/test_v04_social_life_core.py` 全绿。

## 2026-10-29 收尾：正式生命周期与同一本账

原单交的是"正式组件都可用"，但 demo 用 `new Resident()` + 手填 `assembled={"contact_port": ...}`
起 lane，另给回复器造了一个 8 次上限的临时预算根——所以"标准 `start()` 自己起 lane / `stop()`
自己停"和"回复扣的是实例那一本账"都没被证。

现在把这两件补实：

- **唯一注入口**：`AutonomousLifeResident(..., local_reply_client=None, local_reply_requests=None)`，
  `start()` 原样传给 `assemble_autonomous_life(...)`。默认 `None` 分别表示"解析真实 provider"与
  "用本实例自己的持久账本"——不新增第二个预算类、不改上限、不换 run。
- demo 去掉临时预算根，回复器直接吃 `assembly.request_budget(env, state_root)` 那一本；新增
  第 13–15 段在一个全新 root 上走正规 `start()`：lane 自动起（`running=true`）、回复器
  `budgeted=true` 且 ledger 路径/run 与实例一致、受控阻塞期间回复归档且账本只 +1、同消息重投
  不再扣、lane 已 claim 时主 tick 认领不到（`CONTACT_EVENT_CLAIMED`）、`stop()` 后线程结束
  且 `runtime` 归零。

仍不证：真模型自然采用、G1、24h；第 13–15 段的"步骤"是受控等待事件，真 AR-0 在途阻塞由
第 4–6 段的真 owner 链证明。
