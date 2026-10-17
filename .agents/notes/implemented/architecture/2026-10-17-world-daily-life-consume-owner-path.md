# Agent Note: CONSUME 从 Life 决策到 World owner 的唯一通路（verb 白名单最小登记）

Status: implemented

## Problem

F 包把 `CONSUME {object_id}` 落成了正规 verb：Resolver 有 `_resolve_consume`、gateway 有候选与 `get_action_result` 回执、substrate 在同一原子保存里删 placement 并落回执。但**从 Life 的决策出发根本走不到它**，因为正式通路上的三个白名单都没登记这个 verb：

1. AR-0 的 `WorldActionAdapter.validate_prepare` 只接受 `{"MOVE", "POSE"}`，所以一个真实的 `CONSUME` payload 在 AR-0 自己的合同层就被拒（`unsupported WORLD_ACTION verb='CONSUME'`），连提交栅栏都到不了；
2. 桥的 `action_intent.ACTION_ALLOWLIST` 是 M5/M15 定下的显式清单（`MOVE/POSE/PICK_UP/PLACE`），`build_submission` 对不在清单里的 verb 一律 `INVALID`——"加进 Resolver 不等于获得许可"正是它写下的规矩；
3. `JourneyWorldResolver` 只声明 `SUPPORTED_VERBS = ("MOVE",)`，而它正是生产装配里 AR-0 用的那个 resolver；一个消费请求会被它当成"单 target 表达不了的 verb"拒掉。

结果是：F 的能力只能被测试用 `service.gateway_surface.submit_action` 直接触发；任何"她真的决定吃掉一份东西"的日常都无处落地，且这个缺口不会自己暴露（测试直接调 resolver 也能过）。

## Decision

在**三个白名单**上做最小登记，语义与形状都不新开：

- `world_action_bridge_adapter.SUPPORTED_VERBS = ("MOVE", "CONSUME")`，`VERB_PARAMETER` 加 `CONSUME → object_id`：AR-0 的单个 `target` 字符串对 CONSUME 就是那个 `object_id`，所以映射是全的，没有第二参数要造。
- `world_journey_action_adapter` 新增 `DELEGATED_VERBS = ("CONSUME",)`：消费不是旅程（没有路段、没有到达），因此它把该 verb 原样转给 World 自己的正式授权入口 `WorldBridgeResolver`（Intent Executor → `gateway.sock` → Resolver），执行身份仍由本 resolver 的确定性 `execution_id_for` 派生，UNKNOWN 仍可对账。对账也按 verb 分流：旅程账本答不了委托 verb，`query_status_for_reconcile` 改问 owner 的执行结果。
- `action_intent.EXPOSED_VERBS` / `ACTION_ALLOWLIST` 两处（`components/world/service/` 与 `.../bridge/` 两份同内容副本）加 `CONSUME`；`test_action_intent` 里那条"清单恰好是这四个"的断言同步更新，负控（DROP/THROW/GIVE/… 仍不在清单里）原样保留。
- AR-0 的 `WorldActionAdapter.validate_prepare` verb 集合加 `CONSUME`，payload 键仍只有 `verb` / `target` / `expected_dependencies`，一个字都没多。
- 消费成功后的 Life 侧语义单独一条：`_classify_world_consume` 只认 owner 回执（`receipt_present` + `object_id`），用回执自己的 `object_id` 向 LR-5 提交 `world_fact:object:<id>:consumed`；**绝不**把"物件不见了"当成功，也绝不把一次消费报成到达（`journey` 字段不出现，位置不动）。`_classify_world_step` 因此按 payload 的 `verb` 分流，MOVE 的 `arrived:false → IN_TRANSIT` 分支原样保留。

## Alternatives considered

- **让 Life 侧直接调 `WorldResolver.resolve` 或 `service.act`** — 改动最小，测试里本来就这么干。否决：那正是"绕过唯一提交权威与一次性授权"的旁路，执行身份、新鲜度检查、重放保护全丢，和"正式动作必须走 owner 授权链"的既有裁定冲突。
- **让 Life 侧自己判"这份能不能吃"再走 MOVE 之类** — 不用动任何白名单。否决：可食判定归 World owner（声明块 + live placement + 营业 + 姿态），Life 侧自判等于造第二份可食真相；而且 F 的候选列表本来就是 owner 的判定结果，重复实现只会漂移。
- **把 `CONSUME` 也做成计时旅程的一种** — 复用现成的 journey 分支，看起来统一。否决：消费没有路段、没有在途时间，把它塞进 journey 会让"出发/到达"这套物理事实被一次瞬时 owner 变更污染；反过来也让旅程账本出现永远不该有的段。
- **把消费折成 `POSE`（坐下再"吃"）绕过白名单** — 完全不改白名单。否决：`POSE` 只改姿态，改不了 placement，也留不下消费回执；那等于用姿态假装吃过，正是要禁止的"编造后果"。
- **只在桥的清单里加、不改 AR-0 与 journey resolver** — 少改两处。否决：AR-0 的 `validate_prepare` 会先把 payload 拒掉，加桥的清单根本到不了 Resolver，属于"改了个看不见的地方"。

## Consequences

- **收益**：`CONSUME` 第一次可以从一次真实的 AG-1 Decision 出发走完整条正式链：AG-0 机会 → AG-1 Decision → AG-2 Adoption → AR-0（proposal/fence/attempt/receipt/reconcile）→ Intent Executor 铸一次性授权 → `gateway.sock` 消费 → Resolver 提交 → owner 回执 → LR-5 世界事实。`tests/test_world_daily_life_consume.py` 用真实 `WorldBodyService`（真 lease、真授权注册表、真 substrate）证明了一份 instance 真的被用掉、回执与执行身份对齐、Body 恰好一条后果、Activity 真的结束并释放前台。
- **代价与已知上限**：白名单是显式的，所以每加一个 verb 都要改三处（AR-0、桥清单、journey 委托），这是刻意的代价而不是疏忽；`test_action_intent` 的清单断言也必须跟着改，否则门禁会拦住。
- **已知上限**：`build_action_space` 仍然不产出 `CONSUME` 候选（World 侧的 selector 工具路径因此看不到消费），Life 侧读的是 gateway 自己的 `get_available_actions`——两个入口今天不冲突，但若将来要让桥内 selector 也能选消费，得在 `build_action_space` 里按 PICK_UP 的样式补一段。
- **已知上限**：`CONSUME` 的经济/份量语义仍归 F 的范围（一份 = 一个 instance、经济物件一律拒、坏 economy fail closed），本笔记不扩大它。
