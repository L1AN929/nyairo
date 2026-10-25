# Agent Note: 只有 World 可走时，单路径分派缺少候选类型预检

Status: implemented

## Problem

多路径轮次按已提交 Decision 自己的候选路由：`_routed_step_builder` 与
`_RoutedAdoptionSpec` 都先问 `_candidate_is_world`，只有 place/object/stay/bodyact/dailyact
五类目标前缀的候选才会进入 World builder。只有 World 一条路径可走时（没有可续作品、没有阅读材料、
没有收尾或私人项目），`AutonomousLifeCycle` 直接绑定 `_DecisionBoundStepSpec(self._world_step_spec)`，
只在 WORLD 可用的 adoption 分支也直接调用 `_world_adoption_spec`，两处都没有这层预检。

于是模型若在这一轮选了私人机会候选，单 World 路径会先为观察到的地点构建 World Activity 形状，
再在 `_world_step_spec` 里才发现“没有 World 目标”并抛既有
`the committed Decision %r names no observed World target`。2026-10-22 的一次真实日常段
（`dec:f1ca026164e400f0`，START 选 `cand:a077fcadc6750011`，PERSONAL_OPPORTUNITY）报告的正是
这条拒绝。历史完整的 target_refs/step_path 没有留存，不能把这次修复当成该故障全部因果已定；
这里修的是已证实的“拒绝前已构建 World 形状”这一防误送缺口。

复现（离线、仅 WORLD 路径 + 私人候选）：修复前 World adoption builder 与 World step builder
各被调用一次，随后才以同一文本拒绝。多路径分支没有这个问题，因为它的路由谓词先于 builder。

相关的既有决定见
[2026-10-17-world-daily-life-opportunity-routing.md](../feature/2026-10-17-world-daily-life-opportunity-routing.md)：
World 候选按 target 命名空间分流，`object:`/`place:` 等前缀就是判别依据。

## Decision

1. 新增 `_require_world_candidate(decision_record)`：读取已提交 Decision 选中的候选
   （复用 `_selected_candidate`），用既有 `_candidate_is_world` 判定。不是 World 候选、
   或候选读取失败，就抛既有 `AutonomousLifeError`，文本与 `_world_step_spec` 当前拒绝一致，
   不新增错误码、持久字段或候选可执行性权威。
2. 单 WORLD 分派的两个 builder 都先过这道门：`_world_step_for_decision` 与
   `_world_adoption_for_decision`。因此拒绝发生在任何 World Activity 形状或 step 被描述之前，
   也不会落到私人事项/阅读/作品 fallback。
3. 多路径 `_routed_step_builder` 与 `_RoutedAdoptionSpec` 不改：它们本来就在
   `_candidate_is_world` 为真后才调用 World builder。
4. 合法 World 候选（包括私人候选记录上携带的合法 World extras）仍按原五类前缀与
   `_world_step_spec` 匹配；不按 `candidate_kind` 标签二次判定，不重新选候选、不重试、
   不写回原 Decision。

## Alternatives considered

- **直接复用 `_routed_step_builder`**：立论是少写一份逻辑、与多路径完全一致。否决：它的最后
  一段 fallback 是 `_work_step_spec_for_decision`，单 WORLD 轮次下会把私人候选送进私人内容
  承接，正好扩大本工单要关闭的误送面。
- **把预检放进 `_world_step_spec` 开头**：立论是覆盖所有调用点、代码最少。否决：adoption 形状
  在 step 之前构建，只在 step 内检查仍会先产生 World builder 效果；而且单测无法区分“预检拒绝”
  与“builder 内部拒绝”。
- **按 `candidate_kind` 标签判 World**：立论是标签语义直白。否决：既有谓词按 target 前缀，
  历史候选构造允许合法 extras 携带 World 目标；按标签会把可承接的合法候选挡掉，等于新造规则。
- **新增专门的拒绝码/持久字段**：立论是下游更容易区分。否决：本轮是防误送定点修复，沿用既有
  错误文本和 `OUTCOME_DECISION_FAILED` 已足够，不新增跨模块契约。

## Consequences

- **收益**：只有 World 可走时，非 World 候选在任何 World builder 之前被诚实拒绝；
  负控可观测到 World step/adoption、私人作品、阅读、项目 builder 全部零调用，决定只问一次、
  不被改写。
- **代价**：合法 World 候选会多一次候选读取（预检一次、`_world_step_spec` 再读一次）；
  两次读取之间若候选变化，后一次仍会诚实拒绝，不会误送。
- **上限**：这只防误送和给出诚实拒绝，不保证私人机会本身有可继续的执行路线，也不证明
  历史故障的根因已全部定位。Memory UNBOUND、understanding UNKNOWN、EP-09 恢复 FAIL 等边界不变。
