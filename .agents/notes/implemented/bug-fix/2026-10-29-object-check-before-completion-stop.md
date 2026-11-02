# Agent Note: 完成前对象核对走既有正式 API 即可组装（场景 6）

Status: implemented

## Problem

设计 v2 第 14 章场景 6 要求“当前动作成功而对象核对冲突，活动不被误完成”。仓库里 LR-5 已有
`CONFLICTING_EVIDENCE_BLOCKS_COMPLETION`：绑定本 Activity 的 CONFLICT 证据会让完成评估返回
`CONFLICT`，不产生完成资格。

首轮严格反例只看了 `cycle.evaluate_once(act=True)` 这一条入口，观察到它在**同一回合**完成
“Decision → AG-2 采用 → AR-0 归档 → AR-1 结算 → LR-5 完成”，于是写成“没有调用者可见接缝、
唯一入口、不存在自然来源”。这个结论把两棵树的事实差异当成了全树结论：`act` 与 closeout 形状
的耦合只发生在 cycle 这条路径，不代表 AG-2 的 adopt / execute 和 AR-1 的证据接纳之间没有别的
正式接口。

## Decision

用树里已有的正式 API 分步组装，不新增协议、不改状态机：

- `run_decision_and_adoption_cycle(..., adoption_spec=closeout_spec, execute_initial_step=False)`
  提交真实 `START` Decision 并采用成带完成合同的 `CLOSE_PERSONAL_ARTIFACT`，不执行；
- `create_step_and_propose_action` + `submit_step_action_and_apply_post_submit_behavior` 让 owner
  真归档、AR-0 落真实回执、Action `SUCCEEDED`；
- `settle_and_propagate_action_result(additional_evidences=[...])` 在 LR-5 评估前交付一次对象
  复核（fixture，明确标注），AR-1 判 `CONFLICT` / `UNKNOWN_EFFECT`，LR-5 保持 Activity
  `ACTIVE`，integration 标 `CONFLICT`。

`scripts/action_object_conflict_demo.py` 以退出码 0 报告 `SCENE_HELD`；同时保留错 `action_id`
绑定被 AR-1 具名拒绝、同一复核幂等重放、无冲突时真的 `COMPLETED` 的对照，以及
`infra_settlement_review_cli.py` 可读到冲突原因。`tests/test_action_object_conflict.py` 固化
AR-1 冲突/一致两组单元与原有三组规则事实。

不把“场景可组装”写成“新增了完成前协议”：本轮**没有改** AG-1/AG-2/cycle/AR-0/AR-1 的行为。
先前一轮新增的 `progress_state` / 三轴读面 `completion_reason`、`completion_evidence_refs`
纯读投影保留，它们是唯一落地的实现变化。

## Alternatives considered

- **给 closeout 一个 adoption-only 正式入口**：最强理由是它让“只采用不执行”变成一条命名路径；
  但 `execute_initial_step=False` 已经提供同样的分步采纳能力，另造入口是重复契约，否决。
- **在 `act=False` 采用后手工注册 closeout 完成合同**：等于给开放式
  `CONTINUE_PERSONAL_ARTIFACT` 塞一个它不拥有的完成合同，语义错误，否决。
- **先把 Activity 设成 COMPLETED，再用迟到纠正演示**：那是场景 7 的路径，任务书禁止用它冒
  充场景 6，否决。
- **新造对象核对 owner/协议**：既有 AR-1 `additional_evidences` 就是第二次观察的正式入口，
  无需新协议，否决。

## Consequences

- 场景 6 有可运行的正控、对照、错绑定负控、幂等与 CLI 读面；不再以“缺口”交付。
- 对象复核是 fixture，非自然并发观测；Decision/内容是脚本替身；不称 EL4 / G1 / 24h /
  完整场景 6 的自然采用。
- 边界：探针证明的是“既有 API 能分步组装”，不证明生产 resident 会自然产生第二次观察；
  也不证明任何 observer 都能穿过 LR-5 binding（错误 target 会被 binding 过滤或 AR-1 接收，
  取决于通道，本轮只验证了 `additional_evidences` 通道）。
- 未跑全库或其它套件。
