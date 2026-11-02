# Agent Note: 维护动作回执进不了个人经历准入

Status: implemented

## Problem

总纲 14§5 场景 10 要求：测试/维护动作真实改变 World，却不被接纳为千代的个人生活。

现状核对（只读）：

- World 侧的维护动作走现成 `WorldBridgeResolver.resolve_action`，真实 owner 会写
  `wauth:` 授权、执行、消费票、推进 body revision，但**不经过** Decision/Adoption/Activity，
  因此没有 Activity 就无法 `emit_experience_handoff`（AG-2 该入口先读真实 Activity，
  读不到直接拒），个人 life 链本来就进不去。
- 真正的漏接在 Memory 准入这一层：`MemoryAdmissionExperienceConsumer.receive_experience_handoff`
  只检查 `handoff_id`。任何映射——包括一条只有 `world-exec:` / `world:` 引用的维护回执——
  都会被写成一条 `life:` origin 的 M0 经历。
- 只补 `activity_ref` 非空是**不够**的：伪造事件可以指向一个真实存在的 Activity。
  上一轮交付把这一点记成「需要用户另批新装配契约」，实际不需要新合同——
  把 owner 已有的只读读口接到 consumer 上，是这条准入规则本来就要的内部接线。

## Decision

准入前核对**这台实例自己的 AG-2 owner 真的持久过这条 handoff**：

- `receive_experience_handoff` 先要 `activity_ref` 非空
  （`IGNORE / LIFE_ACTIVITY_REF_MISSING`，计数 `refused`）。
- 再用 `handoff_id` 查 owner 的持久记录，并逐字段比对
  `activity_ref / activity_revision / activity_kind / terminal_or_milestone_kind / subject_id`
  与事件自带的全部 refs、`owner_confirmed_context`、时间戳。不一致即
  `HANDOFF_SOURCE_MISMATCH`；查不到即 `HANDOFF_SOURCE_NOT_FOUND`；读口没接即
  `HANDOFF_SOURCE_UNVERIFIED`；读失败即 `HANDOFF_SOURCE_UNREADABLE`。四者都在 M0 写前拒，
  计数 `unverified`。
- 读口由生产 runtime 构造时自己接上：`ProductionLifeRuntime.persisted_experience_handoff`
  读既有 `integration_read`（`handoff_events_by_id`），在同一个构造里 `bind_handoff_verifier`。
  居民与其它生产装配都走这个构造，所以注入的 consumer 不能绕过核对；默认的
  `RefusingExperienceConsumer` 保持不变。
- AG-2 的正常时序天然满足：`emit_experience_handoff` 先持久化再 dispatch，消费者被叫到时
  owner 记录一定已在盘上。第一次投递不受影响。
- 不新增 schema、不新增 origin 旗标、不加来源签名协议、不改 Memory 准入政策；
  既有 M0 去重（`source_ref`）、幂等、拒写语义全部保留。

## Alternatives considered

1. **只保留 `activity_ref` 形状校验，另批一次「读 Activity owner」的新装配契约。**
   这是上一轮的结论。否决原因是它把一个已经存在的只读口（`integration_read`）当成新合同：
   核对「这条 handoff 是不是我签的」本来就是这条准入规则的内部接线，不是产品决定。
   而且形状校验挡不住「伪造事件指向真实 Activity」。
2. **让 consumer 直接读 Activity owner，逐个核 `activity_ref` 是否存在。**
   否决：Activity 存在也不代表这条 handoff 属于它——伪造者仍可挑一个真实 Activity。
   按 `handoff_id` 查 owner 自己的持久记录更准确，且不用给 consumer 第二条 owner 依赖。
3. **在 Memory 里加 `origin=test/maintenance` 字段或表。**
   否决：新 schema + 新来源协议，属于「发明旗标当证明」。
4. **全局关 Memory / 让测试路径不走 M0。**
   否决：用「记忆关着所以没写」造空真，恰好是任务要排除的失败模式。

## Consequences

- 维护/测试动作的真实世界效果保留（POSE 真改、票 `CONSUMED`、body revision 0→1），
  但不产生任何 AG-2 交接、没有 Activity 账本、Memory 0 条；重启后世界效果还在。
- 正控改成真实链路：真实 Supply owner + 真实 runtime 的一轮 Life 活动，由真实 AG-2 owner
  签发并持久 handoff，同一 consumer 走原 M0 准入 `ACCEPT`，重投只 `DUPLICATE`。
  伪造 actv、未知 id、篡改真实 id 三种冒充都在写前被具名拒绝。
- **信任范围是本地**：以同一 uid 运行的进程本来就能改写 owner 自己的文件，那不在本检查的
  承诺里；跨 uid 攻击面没有新承诺。
- 代价：任何独立构造 `MemoryAdmissionExperienceConsumer` 却拿不到 owner 读口的旧夹具，
  现在会如实拒写。少数只做 Memory 准入单测、用手造 dict 当来源的旧测试因此不再成立，
  需要时改成由真实 owner 发 handoff，不能靠「兼容旧默认」把核对关掉。
- 兼容收口：`tests/test_life_continuity_inputs.py` 这组不测「真实 owner 签发」本身，
  它测试续接读面，所以由 fixture 扮演 owner——每例投递前用
  `LifeExperienceHandoffEvent.from_dict(...).to_dict()` 登记一条持久记录，consumer 只通过
  读口 `.get` 看见它，仍逐字段比对；未登记 id（`HANDOFF_SOURCE_NOT_FOUND`）与篡改副本
  （`HANDOFF_SOURCE_MISMATCH`）在本文件内也有负控。这是测试边界的依赖替身，**不是**
  AG-2 真实 owner 正控；真实正控仍是上面的 demo。生产 consumer 无读口依旧 fail-closed。
- 发行验收接回正式来源（2026-11-01）：`components/check_life_memory_v02.py`、
  `tests/test_v04_memory_product.py` 的 ConsumerOwner/ConfirmedContext，以及
  `tests/test_activity_external_stop.py` 的 CANCELLED 投递，共用新夹具
  `tests/fixtures/memory_owner_handoff.py`：真实 `IntegrationStore` / lease / capability /
  `IntegrationCommitPort` 加真实 `emit_experience_handoff` 签发并持久，consumer 用同一
  `IntegrationReadPort` 核验。不再用手造 dict 冒充来源，也没有关闭来源核对；未知 id 与篡改
  已比对字段的负控仍在 M0 写前拒绝。
- 仍有三个 consumer 投影层用例留在测试自有 owner store（依赖替身边界，不是真 owner 正控）：
  `outcome_confirmed` 非字面 `True`、未知键如 `self_note`、非 mapping。理由是 AG-2 的
  `validate_owner_confirmed_context` 在持久化之前就拒这三种形状，真实入口不可能承载；
  这些用例只验 consumer 自己的投影拒绝，不冒充来源已核验。
- Decision/内容为脚本替身；owner/runtime/Memory 真。不证自然采用、G1、24h。
