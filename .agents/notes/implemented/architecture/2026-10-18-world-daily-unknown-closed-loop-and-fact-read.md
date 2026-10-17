# Agent Note: owner 答 UNKNOWN 时 Life 用同一次执行对账，营业事实直接进决策输入

Status: implemented

## Problem

父级一次必要验收没通过，失败的三点都不是"再跑一次就好了"：

1. **`UNKNOWN` 没有闭环**。一次真实的出门，World owner 答 `UNKNOWN`（真的提交过、答复丢了），
   Life 侧只把这一轮报成 `STEP_UNKNOWN`，然后**下一轮又发了一次 `START`**，被 AG-2 合法拒成
   `CANNOT_START_WHILE_FOREGROUND_ACTIVE`。中间那段"先对账"只是把 journey 结算了一下，既没
   对账那条 action，也没结算原来那个 Activity——等于用"再决策一次"掩盖了"上次不知道"。
2. **"闭店改主意"没有因果**。演练里"选停留"是调用者每轮用 `policy=("stay", …)` 直接指定的，
   与 W 的 CLOSED 事实没有任何关系；而用来当闭店证据的 `place:cafe` 从 `street_front` 根本不
   相邻，它的缺席来自拓扑，不是营业状态。
3. **M0 要求没做到**。演练 §9 只筛出 `artifact_ref == work_id` 的**作品**行，证明的是"已有的私人
   作品历史"，不是"本次 World 出门/停留/消费的后果进了 M0、并影响了下一次输入"。而且
  `experience_provider` 根本没接，模型从来收不到 `prior_experiences`。
  （I-r3 更正：这只对测试基类 `assemble()` 成立；产品入口 `autonomous_life_resident._memory_ports()`
  是接了的，见 `2026-10-18-world-daily-world-consequence-wording-and-wiring.md`。）

## Decision

### 对账用同一次执行，不重发

`autonomous_life_cycle.reconcile_unknown_step(action_id=…, occurred_at=…, reason=…)` 严格按 owner
自己的顺序走三步，三步都不是重试：

1. `settle_world_journey(observed_at=…, settle_only=True)`：journey 账本按 owner **真实 travel
   记录**回读（`_reconcile` → in_flight/arrived），`settle_only` 保证这一趟绝不会顺手发起新段；
2. `runtime.action_command.reconcile_action(action_id=同一条, occurred_at=…)`：AR-0 的
   World-ledger lookup，同一 action id、同一 idempotency key、同一 execution id、同一 fence；
3. `runtime.coordinator.settle_and_propagate_action_result(action_id=同一条)`：结算**原来那个**
   Activity，LR-5/完成契约看到的与同步答复完全一致。

`STILL_UNKNOWN`/冲突时只报 `reason_code=STILL_UNKNOWN`：不声明成功、不结算、也不发起新尝试。
测试用例把"世界真的出发了、答复丢了"做成一次性的注入（`LostAnswerResolver` 返回 AR-0 自己的
`TRANSPORT_ERR_AMBIGUOUS_POST_SUBMIT`），所以闭的是**同一次执行**，而不是靠 sleep 多跑几次碰绿。

### 营业事实是"事实通道"，不是路由

`world_observation_facts()` 把 W 目录（与 Life 同一个注入时刻、同一份 `last_world_catalog`）里
**这一轮真能选到的地点**的营业状态写进 AG-1 的 `observed_facts`，键形如
`world_place_<place>_availability_<STATUS>`（ASCII+下划线，生产 prompt 的 `observed_fact_keys`
会保留）。它不产生候选、不带 grant、不指定动作；候选仍然是 AG-0/AG-1 给的，"选哪个"是模型那侧
一条固定规则。于是同一地点、同一意图、同一条规则在 CLOSED 与 OPEN 下会给出两个不同答案——这才
是"闭店驱动"的证据，而不是 Life 写了个"闭店必去 X"的路由。

### World 后果走既有 handoff 进 M0

`_record_world_progress` / `_record_world_consume_progress` /
`_record_world_stay_progress(complete=True)` 在 owner 确认之后各发一次
`emit_experience_handoff(world_refs=[…])`：到达/停留/消费这些**没有作品**的后果，原本一条
handoff 都没有，Memory 只能学到"文档版本变了"。Memory 仍由 `chiyo` 准入（不写行、不改 schema、
不加通用 history owner），Life 只是把 refs 交出去；`cycle.experience_provider`（内存侧
`memory.port.prompt_view`，I-r3 起是它——模型看到的那份形状）再把它作为 `prior_experiences` 喂回下一轮决策。

## Consequences

- 一次 `UNKNOWN` 不再产生第二次 `START`，也不再留下 `UNKNOWN` 动作；对账结论是 owner 账本的原话。
- 但对账后 AR-1 对这条**无回执**的动作只能给 `settlement_status=OPEN` / `outcome_kind=UNKNOWN_EFFECT`
  ——没有回执就不假装结算成功；真正的到达/后果由随后的 owner 观察确认。
- 为什么 owner 偶尔会把真的提交过的出发答成 `UNKNOWN`，根因仍在 World intent/gateway 提交路径，
  不归 Life 修；多段长链在它修好之前都不是稳定证据。
- 真实 M0 行的结构化引用（`action_ref`/`owner_result`/`outcome_kind`）是对的；叙述模板在 I-r3 起
  按证据分流：**有 `world_refs` 且没有 `artifact_ref`** 的后果用中性 World 句（不再说"真实修改/新版本"），
  作品行的文案一个字没动，未决结论照样降级成"结果还没有确定"。
- `assemble_autonomous_life()` 只是接受并转交 `experience_provider`；真正把它接上的是 resident
  （`_memory_ports()`，没配 Memory DB 时 `(None, None, None)`）。I-r3 更正了本笔记原先"产品没接"的判断。
- "等开门"仍然没有 owner 侧的 due-time 机制：她只能换一个合法动作，不会在关着的门口自动重试。

## Alternatives considered

- **把 UNKNOWN 当失败，直接放弃这一程**：会把"世界其实已经出发"丢掉，也会让一次真实出行变成
  假失败。反过来"当成功"更糟：那是把 absence 升级成后果。二者都拒绝了。
- **在 Life 里按经验做"闭店就回家/就停留"**：这就是父级明确禁止的固定 router；而且它会把
  "读事实"这件事从模型那里拿走，之后再也没法区分"读了才改"和"本来就打算这样"。
- **给 M0 加一张"World 经历"表**：不需要——既有 handoff 已经能承载 world_refs，问题只是没人发。
- **sleep + 重试到绿**：父级明令禁止，而且那会把 owner 的"不知道"藏起来。

## Evidence

- `tests/test_world_daily_life_continuous.py`：`STEP_UNKNOWN` → `RECONCILED_SUCCEEDED/SUCCEEDED`
  （注入消失答复的那一次）、`list_actions(status="UNKNOWN")==[]`、没有第二个 START；门前街那一轮
  的 `observed_fact_keys` 带 `world_place_reading_point_availability_CLOSED`，`place:reading_point`
  仍在候选里却**没被选**，改成就地停留并真的 START；本次消费的 `actn:…` + `rset:…` 在真实 M0
  查得到（`artifact_ref=null`），下一次真 DecisionRequest 的 `prior_experiences` 带着它，
  随后的 RESUME/CONTINUE 真的落成新 Supply 版本。
- `tests/test_world_daily_life_plan_change.py`：同一地点/同意图/同规则的 CLOSED→停留、OPEN→真的
  走过去、到店后 `stay:reading_point:READ_HERE:600` 才出现并被选中并 START。
- 回执：`docs/engineering/world-daily-i-r2-result.md`。
