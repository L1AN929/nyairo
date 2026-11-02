# Agent Note: 原子步骤期间的普通聊天 —— 回照常，前台不动

Status: implemented

## Problem

v2 总纲 14 章场景 2 要求：她正在做一件不能被打断的事（ATOMIC 步骤）时，普通聊天仍然
可以回应，但**不能**被这条消息非法 pause。

树上两件事各自存在、互相没接上：

1. LR-3 的 ``InterruptionPolicy.evaluate_event`` 早就实现了两条互斥分支：
   ``MESSAGE_CLASS_EXPLICIT_HOLD_REQUEST`` → ``PAUSE_AT_BOUNDARY``（登记待处理，不立即停），
   ``MESSAGE_CLASS_ORDINARY`` → ``NO_INTERRUPT`` + ``BOUNDARY_DEFERRED``（前台继续，边界后
   才动）。规则齐全。
2. 真正被 resident 每 tick 驱动的普通消息路径是
   ``LocalContactInbox`` → ``SocialContactPort.tick()`` → ``_ordinary_message()`` →
   ``FiniteCoPresence.observe()``。这条路**从不读 LR-3 的沟通可用性**：``observe`` 只回一句
   "observed only，reply 若存在是只读表达"，既不判能不能回，也不区分 ATOMIC。

结果是："原子期间她能不能回消息"在运行时是个没有答案的问题。``handle_ordinary_chat_turn``
（AG-2 上的同名语义）有定义但**没有任何调用者**，接上也拿不到现成的 contact 通道。

## Decision

**1. ``FiniteCoPresence.observe`` 纯读 LR-3 的裁决。** 用 ``runtime.interruption_coordinator
.get_current_life_view()``（纯读，不写 assessment、不建 pending），把
``communication_availability`` 投影进返回体，并派生三个字段：

- ``respondable``：availability ∈ {OPEN, LIMITED, BOUNDARY_DEFERRED}。"可以回"；
- ``boundary_deferred``：availability == BOUNDARY_DEFERRED。"现在回，但前台到边界前不动"；
- ``foreground_atomic``：当前 interruptibility 是不是 ATOMIC。

读不到 life view 时 availability 为 ``None``、``respondable`` 为 ``False``，并带上
``life_view_error``——不知道就是不猜 OPEN。

**2. 回应由消费方记录，文本是注入的替身。** ``SocialContactPort`` 多一个可选 ``responder``：

- 默认 ``None``：不编词，``_local_reply`` 返回 ``responded=False`` +
  ``LOCAL_REPLY_NOT_WIRED``；
- 装了 responder：一条消息恰好调一次，只记录 ``chars`` 与 ``LOCAL_ORDINARY_REPLY``，
  **正文不回传任何地方**；responder 抛错是具名的 ``LOCAL_REPLY_FAILED``，不吞消息。

**2b. 生产装配不注入 responder（2026-10-29 收尾核对）。** ``resolve_local_contacts`` 构造
``SocialContactPort`` 时没有 responder 参数，树上也没有普通消息的回应实现（既有的
``ModelPracticeResponder``/阅读回应 writer 接口是 ``respond(request)``，只服务已打开的
共享实践与私人阅读）。所以真实 resident 路径上每条普通消息都是 ``OBSERVED`` +
``responded=False`` + ``LOCAL_REPLY_NOT_WIRED``；demo 里的回应是组件层塞的脚本替身，
不是生产能力。结果只到 ``tick()``/``runner_tick()`` 的调用方：resident 自己的
``tick()`` 记录只收 events/feed_errors/due/reconciled/journey，**把 contacts 整步丢掉**；
即使注入了 responder，正文也只被计成 ``chars`` 后丢弃，没有本地消费者能拿到她说了什么。
要真回一句并让它可取得，就得新增回复/披露合同——本轮不动。

``_ordinary_message`` 的返回体新增：``responded``、``response_kind``、``response``、
``local_only``、``delivered_to_user=False``、``communication_availability``、
``boundary_deferred``、``foreground_atomic``、``no_pause``、``no_activity``。

**3. 不做的事：** 不新增消息总线、不改 AG-1 schema/AG-2/LR-2 状态机/ATOMIC 政策、不把
``handle_ordinary_chat_turn`` 接成第二入口（它不在 resident 的装配里，接它就要新造运行时
引用），也不让消息触碰 resume/pause/资格。

## Alternatives considered

- **把 AG-2 的 ``handle_ordinary_chat_turn`` 接到 ``SocialContactPort`` 里。** 最强理由：
  名字最贴合场景 2，且它已经会调 ``assess_events``。否决：它在生产 runtime 上没有可拿的
  引用（``assemble_autonomous_life`` 不把 AG-2 实例交给 contact seam），接它需要在装配层新增
  一条跨组件引用；而且它的返回体只有布尔，仍然不回答问题本身。用已接线的
  ``observe``/``_ordinary_message`` 是同一份 LR-3 规则，改动面小一个数量级。
- **在 ``observe`` 里调 ``assess_events``。** 最强理由：能直接拿到完整 assessment。
  否决：``assess_events`` 会持久化 assessment 和 pending，把一次"读消息"变成状态写入，
  违反 ``observe`` 自己声明的"不落任何 owner"。``compute_communication_availability``
  是同一个 policy 的纯函数。
- **ATOMIC 时把 ``responded`` 设成 False（"忙，不回"）。** 最强理由：听起来更"专注"。
  否决：这不是既有规则。LR-3 给的是 ``BOUNDARY_DEFERRED``——被延后的是前台移动，不是回应；
  把回应也延后等于自己发明一条比 owner 更严的规则。
- **缺 responder 时返回一段固定文案。** 否决：那是伪造产品能力；"没接表达层"必须看得见。

## Consequences

- 现在能从 ``SocialContactPort.tick()`` 的返回里读到"这条普通消息有没有被回应、当时前台是
  不是 ATOMIC、边界是否被延后"，以及"没有改动 Activity/Action"——**只对直接调用这个端口/
  ``runner_tick`` 的程序消费者成立**，resident 的 journal 不保留这一步。
- 时序（2026-10-29 核对）：``runner_tick`` 顺序是 maintain → contacts.tick → feeds/wake →
  run_due(act=True)，同一线程串行。步骤阻塞期间到达的消息等下一 tick；resident 没有步骤
  中途的消息 lane。demo 的"在途回应"是测试线程直接调组件入口，不是 resident 并发支持。
- ``observe`` 从"只读分类"变成"只读分类 + LR-3 可用性投影"，仍然零写入。
- 真链证据：``scripts/atomic_chat_boundary_demo.py``（真 Supply owner、真 runtime、AG-1 脚本
  Decision → AG-2 → AR-0，受控阻塞让步骤真实在途，回应记录在 release 之前，释放后只有一次
  回执/一个版本；显式 hold 在 ATOMIC 下只 pending；离开边界后普通消息仍可回）。其中的回应
  是**组件层注入的脚本替身**；同一 demo 第 0 步先证明正式装配在无 responder 时给出
  ``LOCAL_REPLY_NOT_WIRED``。
- 未验证：真实模型自然采用、真实用户收到（``local_only``/``delivered_to_user=False`` 是
  硬事实）、真实 resident 在步骤中途处理消息（当前不具备，需新并发合同）、G1、24h。
