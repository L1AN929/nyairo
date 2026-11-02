# 原子步骤期间的普通聊天（2026-10-29，rev2：补上本地回复出口与在途消息 lane）

一句话：她做不能被打断的事时，普通消息会被观察到、**不会暂停前台**（LR-3 给
`BOUNDARY_DEFERRED`），而且现在**真的会留下她回的那一句**——由 resident 自己的消息 lane 在
步骤在途时处理，回复存在消息 owner 上，可用本地 CLI 按 message id 取回非空正文。

## 现在能用的入口

```bash
# 完整链（真 Supply owner、真 runtime、resident 自己的消息 lane、脚本 Decision/回复替身、0 产品模型）
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/atomic_chat_boundary_demo.py

# 本地消息入口（三条命令：投递 / 看状态 / 取回复）
python scripts/local_reply_cli.py --state-root <life state> --subject <subject> --home <home> \
    send --text "在吗"
python scripts/local_reply_cli.py ... status --operation-key atomic-chat:mid-step
python scripts/local_reply_cli.py ... reply --event-ref contact_evt:... --include-text
```

`reply` 默认只给 `reply_present` / `reply_chars`；正文只在显式 `--include-text` 时输出。
也可以直接用程序口：`LocalContactInbox.submit(...)` 投递、`LocalContactInbox.reply(ref, include_text=...)` 取。

## 回复从哪来（生产已接）

`resolve_local_contacts()` 现在用 `_local_reply_responder()` 构造正式 responder：
和 writer/共享实践同一条 profile 解析、同一个 `OpenAICompatibleTransport`（同一个 pause
send guard）、同一本持久请求账本（`BudgetedModelClient`，一次预留一次请求）。`profile` 不可用时
返回 `LOCAL_REPLY_MODEL_NOT_CONFIGURED` 并如实记 `responded=False`，不拿模板顶替。

`local_message_responder.LocalMessageResponder` 只做一件事：一次 `complete()`，纯文本、有界
（600 字）、无 tool、无动作、无 Memory 写、不取任何私文。`resolve_local_contacts(...,
local_reply_client=...)` 是最底层注入点（只替换 provider 那一层），测试/演示用它。

回复落到 owner：`SocialContactPort._local_reply` 调
`LocalContactInbox.record_reply(event_ref, claim_token=..., text=...)`，只有真的归档成功才
`responded=True`（带 `reply_ref`/`chars`）。失败是具名的：
`LOCAL_REPLY_MODEL_FAILED` / `LOCAL_REPLY_BUDGET_REFUSED` / `LOCAL_REPLY_PAUSE_REFUSED` /
`LOCAL_REPLY_EMPTY_ANSWER` / `LOCAL_REPLY_TOO_LONG` / `LOCAL_REPLY_NOT_ARCHIVED`。
一条消息只有一句：同样的文本重放，不同文本 `CONTACT_REPLY_ALREADY_RECORDED`。

| 字段 | 含义 |
|---|---|
| `responded` | 这句话已经**归档**在消息上，可以按 id 读回来；不是"模型被调用过" |
| `archived` / `reply_ref` | 归档成功与其引用 |
| `communication_availability` | LR-3 的裁决：`OPEN` / `LIMITED` / `BOUNDARY_DEFERRED` |
| `boundary_deferred` / `foreground_atomic` | ATOMIC 前台：现在回，前台到边界前不动 |
| `no_pause` / `no_activity` | 恒为真：消息从不暂停、从不开始 Activity |
| `local_only` / `delivered_to_user` | 真 / 假：没有外发，也没有人被告知用户已收到 |

运输结果保持原语义：只有真归档了才把 `OBSERVED` 的理由写成 `LOCAL_REPLY_RECORDED`，其余仍是
`OBSERVED_ONLY`（为什么没回在返回行与 `port.describe()["local_reply"]` 里）。

## 在途消息 lane

`local_message_lane.LocalMessageLane`：**一个**线程，只调
`SocialContactPort.tick_ordinary_messages()`——只认领 `LOCAL_MESSAGE`，邀请/结束留在串行步骤里。
claim 用 `consumer="local_message_lane"`，所以主 tick 与 lane 不可能同时回同一条。它不跑 cycle、
不碰 AG-1/AG-2/AR-0/LR-2/LR-4、不建/不暂停 Activity、不取 lease、不跨模型 I/O 持锁。

生命周期归 resident：`AutonomousLifeResident.start_message_lane()` 在 `start()` 里起，
`stop_message_lane()` 在 `stop()` 里先停（先于 runtime 关闭），只 signal+join，不杀线程、不清 claim。
`tick()` 的 journal 只多一份计数（`message_lane`），正文与回复永远不进 journal/health/console/prompt。

所以：`runner_tick()` 的串行顺序（maintain → contacts → feeds → `run_due`）没有变，步骤在
`run_due` 里阻塞时，普通消息由独立 lane 处理，回一句话，不碰那一步。

### 正式生命周期与同一本账（2026-10-29 收尾实测）

最底层注入只有一个入口：`AutonomousLifeResident(..., local_reply_client=...)`，默认 `None`
= 解析真实 provider；`local_reply_requests` 默认 `None` = **本实例自己的**持久账本
（同一份 `life_request_budget.json`、同一个 run，与决策/内容共用，不另开独立上限）。
`start()` 自己起 lane，`stop()` 先停 lane 再关 runtime。

demo 第 13–15 段在全新 root 上走这条正规路径实跑：`start()` 返回 `message_lane.running=true`、
回复器 `budgeted=true` 且账本路径就是实例那一本（`utc_day_max=96`，不是另造的 8）；受控阻塞期间
回复已归档、账本只 +1、同消息重投不再扣；lane 已 claim 并卡在生成里时主 tick 认领不到
（`CONTACT_EVENT_CLAIMED`）；`stop()` 后 lane 线程结束、`runtime` 归零。

## 边界

- 「回应」不是「用户收到/已读」，没有外发通道，不写通用 journal。
- demo 的 Decision 与回复都是脚本替身；不证明真实模型自然采用，也不是 G1 / 24h。
- demo 输出 33 项 CHECK；第 13–15 段的"步骤"是一个受控等待事件（不是真 Action）——真 AR-0
  在途阻塞与回复先后顺序由第 4–6 段的真 owner 链证明。
- 回复走既有预算账本（失败不退、无新重试合同）；不做公开 HTTP、不做多受众、不挂披露许可。
- `handle_ordinary_chat_turn`（AG-2 上的同名入口）仍然没有调用者；本单用的是 resident 真正
  驱动的那条 contact 通道，没有新造第二入口。
