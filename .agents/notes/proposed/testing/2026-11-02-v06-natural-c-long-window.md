# Agent Note: C 延长自然观察，不改变静默触发规则

Status: proposed

## Problem

[B结果](../../../../docs/engineering/v06-world-natural-b-result-20261102.md)在30分钟内只有一次明确NO_ACTION，MOVE合法但未选，其后NOT_DUE/QUIET不是反复模型拒绝。B检查间隔实际1分钟；改30分钟会同时改变节奏。身体变化是否进入有效处境与请求触发未证，不能承诺延长必有新请求。

## Proposal

[C预登记](../../../../docs/engineering/v06-world-natural-c-prereg-20261102.md)建议另开新身份/root/run，10800真实秒、保留1分钟检查/5秒tick、新增cap20/history192最坏212，今晚最晚20:00启动、23:05绝对停新提交，收尾60秒。B结束不改、不复用余量。

**离线部分已实装，父级固定入口独立通过（44项/26.941秒，2026-11-02）**：`scripts/v06_world_natural_c.py` + 新测试 + `C_PROFILE`（10800 窗口 / 3000 tick / 6000 trace，旧批 1800/900/4000 不变），固定入口 44 项全 PASS；落地事实见[implemented note](../../implemented/testing/2026-11-02-v06-natural-c-offline-entry.md)。**真实 `prepare`/`run` 仍未批准、未执行**，本篇 proposed 只等真实运行批准。

本次定向历史分类：
- [A/B分层提案](2026-11-02-v06-isolated-world-roundtrip.md)：部分重叠，B的30分钟取舍不改成3小时；本篇记录新的C选择。
- [B离线入口](../../implemented/testing/2026-11-02-v06-natural-b-offline-entry.md)：部分重叠，复用隔离/预算/guard/收尾，不修改B决定。
- [A往返](../../implemented/testing/2026-11-02-v06-world-roundtrip-check.md)：机制证据，非自然变化来源，不重跑。

## Alternatives considered

改30分钟检查最省轮数，但改变B既有节奏，否决。继续30分钟成本最低，但不能增加真实经历时长，本新实验选3小时。无限延长/追加额度最易增加采样，却无法保证触发且成本不定，否决。注入身体变化或改指纹易得第二次请求，但会改变待观察对象/生产规则，否决。

## Acceptance criteria

仅新增C局部参数与现有natural链，离线证明10800/20/时间门/子env/守卫/关闭及旧值保留，不真实等3小时或刷旧套件。自然run另批后分层记录实际变化、请求和动作；未覆盖不补跑，不能据一次请求推没有自然变化源或据多次请求唯一归因身体。

## Risks

更长窗口仍可能QUIET或提前到额/故障停。连续身体量、候选、指纹与有效唤醒不是同一层；现成读口不足保留UNKNOWN，不发明新监控/触发。C 的 3000tick/6000trace 已离线贯通，旧 900/4000 未改；3 小时真实运行与极端收尾仍未验证。数据保留，临时gate退出不撤销所有票或回滚效果；不是完整G1/24h。
