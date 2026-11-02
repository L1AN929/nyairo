# 场景10：维护动作改真实 World，不进个人生活（2026-10-29）

总纲 14§5 第 10 条。一句话结论：维护动作能真改世界，但它的回执进不了她的个人经历；
准入现在核对的是**这台实例自己的 AG-2 owner 真的持久过那条 handoff**，不是映射的形状，
所以伪造 Activity 引用也进不来。

## 现在能用什么

- **维护/测试动作的正式入口**：`world_action_bridge_adapter.WorldBridgeResolver.resolve_action`
  （真实 gateway socket + 真实 registry + 真实世界状态）。gate 默认 `off` 不变；
  只在一个新进程树里显式 `WORLD_BODY_INTENT_MUTATION_GATE=canonical` 时才真提交。
- **个人经历的唯一写口**：`LifeExperiencePort.append_outcome`，由
  `MemoryAdmissionExperienceConsumer.receive_experience_handoff` 在生产里调用。

## 改了什么

1. `components/life/scripts/life_experience_memory_consumer.py`：准入前要求 `activity_ref`
   非空（`IGNORE / LIFE_ACTIVITY_REF_MISSING`，计入 `refused`），再拿 `handoff_id` 去问
   owner 的只读读口；读口没接（`HANDOFF_SOURCE_UNVERIFIED`）、查不到
   （`HANDOFF_SOURCE_NOT_FOUND`）、内容与持久记录不一致（`HANDOFF_SOURCE_MISMATCH`）、
   读失败（`HANDOFF_SOURCE_UNREADABLE`）一律在 M0 写前拒，计入 `unverified`。
2. `components/life/scripts/life_runtime_production.py`：生产 runtime 构造时把自己已有的
   `integration_read` 作为只读读口交给 consumer（`persisted_experience_handoff`）。
   居民与其它生产装配都走这个构造，因此不存在「注入的 consumer 绕过核对」；
   默认的 `RefusingExperienceConsumer` 一个字没改。
3. `scripts/maintenance_experience_boundary_demo.py`：正控改成真实链路——真实 Supply owner、
   真实 Memory、真实 runtime；一轮脚本 Decision 的 Life 活动由真实 AG-2 owner 签发并持久
   handoff，再经同一个 consumer 走原 M0 准入。

## 跑出来的结果

`scripts/maintenance_experience_boundary_demo.py`（全新隔离根 `/tmp/nyairo-result-method-fixtures/...-m10`，保留）：

| 项 | 结果 |
|---|---|
| World 维护动作 | `committed`，pose `standing→seated`，票 `CONSUMED`，body revision `0→1` |
| 个人 life 链 | AG-2 交接 0 条、Memory 经历 0 条（维护动作没有变成经历） |
| 真实 owner handoff | 持久化 1 条，同一 consumer `ACCEPT`，Memory 1 条且可回读 |
| 维护回执冒充 handoff | `IGNORE / LIFE_ACTIVITY_REF_MISSING` |
| 伪造 actv + 伪造 Decision/adoption | `IGNORE / HANDOFF_SOURCE_NOT_FOUND` |
| 引用真实 Activity、owner 无此 handoff | `IGNORE / HANDOFF_SOURCE_NOT_FOUND` |
| 抄真实 id 改来源/目标 | `IGNORE / HANDOFF_SOURCE_MISMATCH` |
| 真实 handoff 重投 | `DUPLICATE`，经历不增 |
| 正规停启后 | 世界姿态还在；合法交接仍可读回；重投 `DUPLICATE`；新增 Action 0 |

Memory 状态 `READY`，且拒维护与接受个人来源在同一条绑定上并存——排除「端口拒写 /
Memory 关着所以零记忆」的空真。

## 兼容层（不是新的实名正控）

`tests/test_life_continuity_inputs.py` 只测续接读面，不测「这条 handoff 是否由真实 owner
签发」；那件事由上面的 demo 证明。该文件现在由 fixture 扮演 owner：每例投递前用
`LifeExperienceHandoffEvent.from_dict(...).to_dict()` 登记一条持久记录，consumer 只能通过
只读读口 `.get` 看见它，并且仍逐字段比对。未登记 id 与篡改副本在同文件内有负控，分别得到
`HANDOFF_SOURCE_NOT_FOUND` 与 `HANDOFF_SOURCE_MISMATCH`。这是测试边界的依赖替身，
不宣称 AG-2 真实 owner 正控；生产 consumer 无读口仍然拒写。

## 没证到的范围

- 信任范围是本地：以同一个 uid 运行的进程本来就能改写 owner 自己的文件，不在本检查承诺内。
- 只验了一条 POSE；没有穷尽所有维护命令或所有 World 写口。
- 生产 runtime 的核对由 demo 实跑；居民长循环没有另起一整轮真实日常。
- Decision/内容为脚本替身，owner/runtime/Memory 真；不证自然采用、G1、24h。

## 复跑

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/maintenance_experience_boundary_demo.py'
```
