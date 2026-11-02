# 动作成功 ≠ 对象核对通过：场景 6 用既有正式 API 组装（2026-10-29）

## 一句话

场景 6（当前动作成功、对象核对冲突、活动不得被误完成）**不需要新协议**：用树里已有的正式
AG-1 已提交 Decision → AG-2 采纳（`execute_initial_step=False`）→ AR-0 提交（真实回执）→
AR-1 `settle_and_propagate_action_result(additional_evidences=...)` 就能组装。Action 真的
`SUCCEEDED`，AR-1 因对象复核冲突判 `CONFLICT`，LR-5 保持 Activity `ACTIVE`。

## 现在能跑的命令

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/action_object_conflict_demo.py
```

退出码 0 = 场景成立（`SCENE_HELD`）；退出码 2 = 演示自身观测与树不符。真实 Supply owner、
真实生产 runtime、真实 AR-0/AR-1/LR-5，新合成隔离根。

## 三条现有事实（本次实测）

1. **正式分步采纳**：`run_decision_and_adoption_cycle(..., adoption_spec=closeout_spec,
   execute_initial_step=False)` 提交真实 `START` Decision，并采用成 `CLOSE_PERSONAL_ARTIFACT`，
   Activity 真的带 `ACTION_RESULT_MATCH` 完成合同，且不执行。
2. **Action 真成功**：`create_step_and_propose_action` + `submit_step_action_and_apply_post_submit_behavior`
   让 owner 真归档（`ARCHIVED`，版本数不变），AR-0 记录真实 provider 回执，Action `SUCCEEDED`。
3. **对象复核走正式入口**：一条对象复核（fixture 明确标注）通过 AR-1 自己的 `additional_evidences`
   进入，在 LR-5 评估**之前**到达；AR-1 返回 `CONFLICT` / `UNKNOWN_EFFECT`，LR-5 返回
   `CONFLICT` / `CONFLICTING_EVIDENCE_BLOCKS_COMPLETION`，Activity 保持 `ACTIVE`、
   `completion_evidence_ref` 为空、integration 标 `CONFLICT`（`SETTLEMENT_CONFLICT_DETECTED`）。

## 原 STOP 判断收窄

早前 demo 只在 `cycle.evaluate_once(act=True)` 这一条入口上观察，得出“没有接缝”并 STOP。
这只是两棵树的事实差异，不是全树只有一条入口：`act=True` 同回合确实完成、`act=False` 确实换
形状，但 AG-2 的 adopt / execute 和 AR-1 的 `additional_evidences` 是分开的正式 API，能在
“Action 成功之后、LR-5 完成评估之前”交付一次对象复核。原 note 的“唯一入口 / 不存在自然来源 /
两者都改契约”这几句过强，已在原 note 内收窄。

## 控制与边界（原任务保留项）

- **错绑定负控**：把复核证据绑到另一个 `action_id` 会被 AR-1 具名拒绝，不跨动作绑定。
- **幂等**：同一条复核证据重复结算为 idempotent replay，Activity 仍 `ACTIVE`。
- **完成对照**：不加冲突时同一归档步骤真的 `COMPLETED`（有 `completion_evidence_ref`），
  证明“不完成”是冲突造成的，不是链路坏了。
- **读面**：`infra_settlement_review_cli.py --state-root <state> --action-id <actn>` 退出 0，
  输出含 `CONFLICTING_EVIDENCE_BLOCKS_COMPLETION`。

## 本单实际改动

- `scripts/action_object_conflict_demo.py`（重写为分步正控 + 对照 + 负控 + 幂等 + CLI）。
- `tests/test_action_object_conflict.py`（新增 AR-1 冲突/一致两组单元；保留原有三组）。
- `docs/engineering/action-object-conflict-20261029.md`、原 STOP note、原 result。

未改 AG-1/AG-2/cycle/AR-0/AR-1 状态机、完成谓词、Memory 准入、gate 与预算。此前一轮新增的
`completion_reason` / `completion_evidence_refs` 纯读投影保持不变。

## 未验证 / 边界

- 对象复核是 fixture，不是自然并发观测；Decision/内容是脚本替身。
- 未跑全库、其它套件或真实产品模型；不称 EL4 / G1 / 24h / 完整场景 6 的自然采用。
- 三轴/CLI 输出只含不透明 ref 与状态，不含正文。
