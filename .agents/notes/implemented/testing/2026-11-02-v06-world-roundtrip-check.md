# Agent Note: 0.6 A 零模型隔离世界往返检查

Status: implemented

## Problem

10 月 31 日 e 已临时 canonical，MOVE 在最终请求里可见，但没有被选择；修好的 d 在 gate off
下又缺 MOVE。再开一次真实模型批次不能只凭未选 MOVE 区分自然选择未覆盖与出门/返程接线缺口，
而完整 G1 还要求中断、跨 PID、UNKNOWN、版本冲突与 NO_ACTION 后续作。A 要先在零模型、
零产品请求、可隔离重跑的根里，把“正式 Decision → AG-1/AG-2/AR-0 → World gateway →
registry/Resolver → owner 位置结算”的出门与返程链跑通；[方案](../../../../docs/engineering/v06-isolated-gate-g1-proposal-20261102.md)
的 B（自然选择）不在本单。

## Decision

新增 `scripts/v06_world_roundtrip_check.py` 与 `tests/test_v06_world_roundtrip_check.py`。
`check --offline` 每次在 `/tmp/nyairo-v06-roundtrip-*` 新根下建两个独立子根（off/on），
子进程用固定白名单环境、只允许本根 AF_UNIX；`HOME`、World/Supply/预算/authority/socket
以及 bridge 的 lifecycle/registry/activity/experience/self-maintenance 五个写面都显式钉在
子根内（缺失/越界写前拒绝），父进程不 patch 自己的 gate 或正式根。

- **off 负控**：沿用既有 `world_refusal_result_shape` 的接法，先在 canonical 下只读取一次
  真实 MOVE 候选（gate off 时 `action_intent.build_action_space` 本就不提供 MOVE），
  随后把 gate 关掉再走正式提交。实测 `STEP_REFUSED` / attempt `PRE_SUBMIT_FAILED` /
  `error_class=GATE_OFF`、Action 仍 `PREPARED`、零回执、零票、零 travel、世界状态字节不变。
- **on 往返**：脚本 Decision 从 AG-1 真给的候选里选 `home → street_1`（默认图唯一合法
  邻近室外地点，kind=street）。出发是 `OUTCOME_IN_TRANSIT`（Action `SUCCEEDED` 只表示
  出发，位置仍在 home）；到达由 `cycle.settle_world_journey` 按 World owner 自己的
  `world_state.json` 结算，第一段 Activity 经既有 LR-5 完成合同变为 `COMPLETED` 并释放
  前台；**新的**正式 Decision 再返回 home。两段各一张一次性票 `CONSUMED`、各自 travel
  `ARRIVED`、两张 Activity 均 `COMPLETED`，最终位置 home。
- **业务钟**：显式注入 `WorldBodyService(clock=...)`，推进到旅程自己的
  `expected_arrival_at`；不冻结全局 `time`/`datetime`、不写新 clock 字段、不改真实 TTL。
  输出标 `clock_basis=TEST_LOGICAL`；真实耗时约 14–17 秒，不是步行时间。
- **验收**：固定命令 `check --offline --max-wall-seconds 600`，父级再只读审计两个子根
  （位置/旅程/travel/票/Activity），不信子进程自报。新测试 10 例只测 CLI/根与路径守卫/
  网络守卫/判据/审计，不重复整链。

## Alternatives considered

- **直接跑 B（真实模型自然选择）**：最强的理由是它最接近“她自己选”，也可能省掉机械验证；
  但 e 已经在 MOVE 可见时没选，单跑一次可能只重复 NOT_COVERED，既定位不了票据/返程接线，
  又要花真实预算和窗口。A 先把机制跑通，B 另批预登记。
- **只调 `world_journey.accept_journey` / `advance_journey`**（`journey_authorization_smoke`
  的现成路径）：最强的理由是它更短、已被验证、变量更少；但它绕开 Life 的正式 Decision、
  AG-1/AG-2/AR-0 与 Activity 采用/完成合同，只能证明 World 引擎，不能证明“正式 Life 链”
  出门再回来——正是本单要补的那一层。否决。
- **只保留 gate off 测拒绝**：最强的理由是零风险；但它证明不了被关闭的正式世界动作在打开后
  能完成出门和返程。否决。

## Consequences

- 证明的是：同一默认世界、同一业务起始时刻下，off 的正式提交被 `GATE_OFF` 明确拒绝且零
  World 效果；on 的两段各有独立 execution/一次性票/travel/owner 位置与 Activity 完成结算，
  最后回到原地点；自有 runtime/World/Supply 正常关闭，零网络、零产品请求。
- 不证明：自然选择、完整 G1（中断/跨 PID/UNKNOWN/版本冲突/NO_ACTION 后续作）、24h、
  真实步行时间或普通用户环境。脚本 Decision 是明确标注的替身。
- `off` 子根会留下 Life 侧那次拒绝的 Activity（World 零效果、零票）；审计如实列出，不当作
  World 副作用。进程退出不撤销已消费票、不回滚位置或作品；所有数据保留。
- 不改 `components/`、既有 scripts/tests/fixtures、默认 gate/配置、旧根、预算或发布物。
  B 仍未批准；本单不申请新预算、不部署。
