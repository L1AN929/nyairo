# Agent Note: 退休的做法不再出现在下一轮可命名的做法里

Status: implemented

## Problem

B 的个人实践工作台已经把退休定义得很清楚：`retire_method` 只写一个 `RETIRED` 版本，文档、
正文、版本和反例全部保留；`record_trial` / `revise_method` 都以 `PRACTICE_METHOD_RETIRED`
拒绝。第三段也证明了证据撤回会扣住正文、`TRIAL` / `CONTESTED` 在下一轮可见。

但 Life 的决定读侧没有把这个损失接上：`PracticeSeam.methods()` 直接把
`list_methods` 的行交给 `decision_context()` / `writing_context()`，再进入
`_round_methods` 与 AG-1 的 `allowed_method_refs`。于是退休之后：

- 下一轮的决定上下文仍然给出该做法的 `method_ref` 和指令；
- AG-1 的允许清单仍然包含这个手柄，命名它不会被拒绝；
- 写作提示仍然把它当作可参考的做法。

结果是"退休"只停在 owner 的状态字上，没有改变她下一轮实际能命名、评价或跟随的方法，
等于用文档还在冒充做法还在。

## Decision

- `life_practice_context.method_availability()` 只把 **`RETIRED`** 判为不可用，理由沿用 B
  自己的 `PRACTICE_METHOD_RETIRED`。这是读侧投影，不新增状态、不写入。
- `PracticeSeam.methods(available_only=True)` 供 offer 使用：退休行不再返回，但会进入
  `last_withheld_methods`；默认的完整读仍返回所有方法（包括退休），给只需要 id 的调用方。
- `decision_context()` 的 `methods` / `method_refs` 只用可用行；若有损失，附
  `withheld_methods`（count / owner 理由 / 标题，最多 3 个），不用空数组冒充"她从来没有过
  做法"。`writing_context()` 同样不再携带退休做法。
  `withheld_methods` 只是既有内部 decision context 的有限投影：不改持久 schema / owner /
  对外 API，也不泄露被治理扣住的正文（只有数量、理由码和有界标题）。
- `autonomous_life_cycle.practice_decision_context()` 只把 `available` 行放进
  `_round_methods`；`practice_method_refs()` 随之不再把退休手柄交给 AG-1。AG-1 对未提供的
  手柄按既有 `INVENTED_METHOD_REF` 拒绝。
- `method_artifact_ids()` 继续使用完整读：退休做法仍是"方法文档"，不能被当成可续写的作品
  或收尾目标。
- 证据读不回来保持第三段合同（行仍读出、正文已被 owner 扣住），不改写成"退休移除"；
  `TRIAL` / `CONTESTED` 仍可用，反例是状态不是损失。

## Alternatives considered

- **在 `PersonalPracticeService.list_methods` 里直接过滤 RETIRED**。否决：这个列表同时是
  owner 的历史/手机读面，退休不是删除；过滤会把"她曾写下并退休"从档案读面抹掉，也会让
  `method_artifact_ids()` 失去"别把方法当作品"的保护。
- **在 AG-1 校验里按状态拒绝**。否决：AG-1 是验证器，不拥有 practice 的可用性；它手上的
  allow-list 仍会先被生产出来，问题只是往后推了一层。
- **用空数组或 `last_error` 表达损失**。否决：空数组会冒充"没有做法"，`last_error` 会把
  owner 的正常状态说成读失败；损失需要自己的名字。
- **新增 `loss_continuation_bridge.py` / 新存储**。否决：这是对既有 owner 行的纯投影，
  放回 seam 即可；新增 store 或第二套状态机没有必要。

## Consequences

- 收益：退休之后，下一轮的决定上下文、AG-1 允许清单与写作上下文真的不再提供该做法；
  owner 文档、版本、反例仍可读，损失被具名而不是被叙事抹平。
- 边界：本改动只覆盖**退休**这一种已有损失；证据撤回保持第三段既有行为，不新增回滚域外
  权威、Memory 高水位或跨备份恢复能力。V-C 恢复 FAIL、G1 未闭合、24h 未做的结论不变。
- 读侧零写；不做旧实例迁移，不自动复活、不重建已撤源或终态作品。
- 回归：`tests/test_loss_continuation_behavior.py` 与
  `scripts/loss_continuation_behavior_smoke.py`。冒烟走正式 `evaluate_once` → AG-1：
  退休前同一命名回答被接受并提交，退休后旧手柄被 `INVENTED_METHOD_REF` 拒绝且没有
  Decision 被提交，同一状态下一次不引用旧手柄的合法 Decision 正常提交；模型 view 带
  `withheld_methods`，writer 视图不含退休指令。既有
  `tests/test_result_method_behavior.py` 的撤回合同保持绿。相关决策见
  [v04 个人实践工作台](2026-11-07-v04-personal-practice-workspace.md)。
