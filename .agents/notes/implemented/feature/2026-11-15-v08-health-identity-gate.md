# v0.8 · 创作阻断的候选身份判定反转

## Problem

主笔记 [[2026-11-15-v08-body-health-envelope]] 最初把“高负担创作”判定为：候选的 `target_refs`/`source_refs` 里出现 `artifact:` 前缀，或 `semantic_identity` 命中一组中英文关键词。这个判定在真实候选上误伤阅读：书店/阅读线索同样携带 `artifact:` 目标（`v02_v03_daily_loop_bridge._make_personal_candidate` 的 `target_ref` 就可能是 artifact），急性期一开，阅读先被拦掉，而真正的工作台草稿反而可能因为关键词写法不同而漏掉。

## Decision

判定改为读候选自己已经携带的结构化身份：

- 先看 `semantic_identity` 的英文 basis：`READING` / `BOOK` / `BOOKSTORE` / `WINDOW_BOOKMARK` / `LIBRARY` 一律放行，即使目标是 `artifact:`；
- 再看 `candidate_kind`：`PURSUE_GOAL_STEP` / `CONSIDER_EXPLICIT_GOAL` 视为高负担创作；
- 再看 basis 里的 `WORKBENCH` / `CREATIVE` / `WRITING` / `DRAFT` / `MANUSCRIPT` / `STUDY_DRAFT`（真实工作台候选是 `WORKBENCH_UNFINISHED_TRAY`）；
- 最后才用中文语义词作补充判据。

`artifact:` 前缀不再单独作为创作证据。无法判定的候选一律保留：这条门禁只拒绝它叫得出名字的创作，不宣称穷举。

同时把 `evaluate_opportunity` 的顺序改为“先创作拒绝、后候选上限”。先截断再拒绝会让前面的创作候选吃满 cap，后面的休息/喝水被无声剪掉。

## Alternatives considered

- **保留 `artifact:` 前缀判定，另加阅读白名单**。最强理由：改动最小，已有测试和 v03 桥的 `creative_intensive` 都按 artifact/关键词打过标。否决原因：白名单仍然是猜测，真实阅读可能用任何 artifact 身份；而结构化的 `candidate_kind` + basis 本来就是候选生成方写下的正式事实，不需要再猜。
- **用中文标题/语义关键词作主判据**。最强理由：中文候选在现有测试里就是这样表达的。否决原因：标题关键词不是候选契约的一部分，换个措辞就漏、换种写法就误伤；它只能作最后的补充。

## Consequences

- 阅读（即使带 artifact）在急性期保留；真实工作台草稿被拒，且拒绝结果仍带 `HARD_REJECT:BODY_CREATIVE_BLOCK`。
- 真实 owner 链（`tests/test_v08_health_decision_chain.py::RealOwnerReadingChainTest`）在 `creative_block=true`、cap=4 下真的执行了 AR-0 阅读步骤并推进私人物品停点；该最小实例没有产生被命名的真实工作台候选，所以同轮 `BODY_CREATIVE_BLOCK` 拒绝数为 0，这不能被读成“创作拒绝已在真实链上复现”。创作拒绝的真实性仍由组件层候选集用例证明。
- 2026-11-15 父级复跑暴露的边界：真实病中一轮的合法低负担候选不止 4 个（绑定作品 continue/closeout、阅读材料、v02/v03 病程照护与发呆、一个 World 观察），`body_candidate_cap=4` 按 `candidate_id` 哈希序取前缀，是否切到阅读取决于实例 UUID，不是创作分类问题。保留全部候选又确定性地把阅读放进这 4 席需要一条新的选择优先级规则（本单不做产品决定）。真实链测试的处理是：本轮显式关闭 v02/v03 日循环机会源（另一个源），让作品×2 + 阅读 + World 观察这 4 个真实候选正好填满 cap；脚本认知改为按 artifact 身份匹配，并在结果断言前先断言“阅读候选真的被 offered”，缺失时以具名事实失败而不是 `NO_ACTION`。
- 先拒绝后截断：四个创作加一个休息、cap=4 时，休息仍进入认知候选集（`tests/test_health_cap_wiring.py` 与 `tests/test_v08_health_decision_chain.py`）。
- 代价：判定依赖生成方写对 `candidate_kind`/`semantic_identity`；无法判定的候选会被保留，这是有意的保守方向。
