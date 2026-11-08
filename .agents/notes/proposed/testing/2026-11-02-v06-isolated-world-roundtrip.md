# Agent Note: 0.6 世界往返机制与自然选择分层

Status: proposed

A（零模型隔离往返机制）已于 2026-11-02 按本人本窗口批准落地，实现与边界见
[implemented/testing/2026-11-02-v06-world-roundtrip-check.md](../../implemented/testing/2026-11-02-v06-world-roundtrip-check.md)。
本文保留A/B分层的原提案与取舍。B其后获本人直接批准并完成唯一真实批，结果为一次明确NO_ACTION、移动未覆盖，见[B结果](../../../../docs/engineering/v06-world-natural-b-result-20261102.md)。以下“未批准/未运行”等为方案阶段记载，不代表当前仍待B运行；不能复用旧授权或余量。另一个3小时新C的取舍单列在[C提案](2026-11-02-v06-natural-c-long-window.md)，不把本篇30分钟决定翻成3小时。

B 的**离线实现**已完成并通过父级独立零模型离线检查（37项/23.605秒/真实子进程POSE正式动作/零产品请求）；worker最终自测约20.7秒另列，不混作父级证据。60秒界限仅验参数与短时超时子进程，未挂起整套服务60秒，
含父进程与两个 owner 子进程的局部网络守卫、只限 B 的坏账 fail-closed 与 60 秒收尾界限，见
[implemented/testing/2026-11-02-v06-natural-b-offline-entry.md](../../implemented/testing/2026-11-02-v06-natural-b-offline-entry.md)。
上述离线证据只证明身份、预算、gate、根内隔离与停机边界。其后endpoint静态核明、唯一真实B获批并结束；MOVE合法但未选、未出门/返程未覆盖，完整G1仍未过。真实结果以B报告为准。

## Problem

10月31日 e 已临时 canonical，MOVE 可见但没有被选择；G1 f 仅限定同作品跨 PID 真实接续。再开一次 gate 不能自动证明自然出门、回家或完整 G1。

## Proposal

[完整方案](../../../../docs/engineering/v06-isolated-gate-g1-proposal-20261102.md)的 A 已通过父级独立固定 check。B复用现成单natural phase、临时子env canonical及真实时钟；20请求包括Decision/内容/失败预留，固定1分钟检查/5秒tick，不清静默凑请求，不强制MOVE或回家。旧入口身份/截止已封闭，故新薄入口和scripts内必要接线已按最小实现完成离线检查，不能直接run旧e。真实endpoint核明后才会单独批唯一真实run；base URL无默认/占位拒绝，不探测模型。具体身份、绝对窗口与写集以B预登记为准；过期不自动延长。生产默认、旧根与Memory合同不动。

历史笔记定向分类：
- [逐段许可](../../implemented/bug-fix/2026-10-23-world-journey-segment-authorization.md)：部分重叠，沿用每段新 execution/票与 UNKNOWN 原操作对账，不改决定。
- [G1 真实采用](../../implemented/testing/2026-10-30-g1-limited-real-adoption.md)：部分重叠，沿用自然选择与脚本故障分层，不续旧批授权。
- [G1 下一次离线判据](../../implemented/testing/2026-10-31-g1-next-offline.md)：部分重叠，已有机制链不重造；世界往返不冒同作品 G1。
- proposed 下定向检索未找到同主题笔记；rejected 目录不存在。未做全树审计，不删除或归档历史记录。

## Alternatives considered

直接复用旧e立即运行最省代码，但旧身份与截止已结束，不得解锁，否决。扩大时间和额度最可能增加观察机会，但更贵且仍不保证MOVE/返程，本稿选20/1800。压缩业务钟能快到达，却改变自然时序，B不选。改默认gate或强制回家容易产轨迹，却扩大权限或污染选择，否决。

## Acceptance criteria

B 的真实运行须先批准完整预登记，唯一新根/固定预算与真实时间窗，不强制行为；按实际 MOVE、到达、返程与票据结算分列覆盖，未出现记 NOT_COVERED，不补跑。A 的脚本结果及旧 G1 分散证据不能拼成自然选择或完整 G1。

## Risks

A 的测试业务钟与脚本决定不能证明自然往返；真实批次可能没选 MOVE 或未返回，均不能推断动机或改规则求绿。数据保留；进程退出不撤销全部票或回滚效果。B的真实模型预算/窗口批准已用于结束的唯一运行，不自动沿用至C。
