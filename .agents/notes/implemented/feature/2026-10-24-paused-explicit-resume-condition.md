# Agent Note: 显式条件 PAUSE —— 由既有词表选条件，owner 满足后才可回来

Status: implemented

## Problem

真实短日 i/j 都停在同一个状态：一次真实阅读后她主动 `PAUSE` 了同一个 Activity，随后
`NO_ACTION`；`PAUSED rev4` 占着前台，`emitted_eligibilities_count=0`、`resume_eligibility_refs`
为空。这不是「模型之后不会再选 RESUME」，而是**没有任何合法机制会把 PAUSED 变成可恢复状态**：

- LR-2 的 `pause_activity` 只做状态转换，并清空 `resume_condition_ref` / `waiting_episode_id`；
- LR-4 唯一的 condition 创建口 `enter_waiting_with_condition` 要求 ACTIVE/PAUSED → **WAITING**，
  而 WAIT 会改变 Activity 状态，不是 PAUSE；
- 既有维护/扫描/资格生成只认 `status == WAITING`；
- B1 的对齐只让「已经存在的 OPEN 资格」在 PAUSED 上不被 reconcile 抹掉，它不生成资格。

只读定位见
[20261024-paused-eligibility-route-result.md](../../../worker-runs/20261024-paused-eligibility-route-result.md)。
用户 2026-10-24 21:33 在本会话明确批准新增这条 PAUSE 语义。设计原文只允许「PAUSED 在各自恢复
条件满足后回来」（SRC-0719:10–13）与把暂停列为合法 transition intent（SRC-0007:16），
**没有定义 PAUSE 的恢复条件内容、谁来选、何时生成**。本笔记记录的是新增规则，不是「旧规则本来
就写了」。

## Decision

**1. 只有一种条件 PAUSE：本轮已提供、模型显式点名的那一个条件。**

- 候选条件不新造词表，就是本轮已经给 WAIT 用的同一个 basis：作品/材料 head 依赖、身体
  `BODY_CAPACITY_RESTED`、或她自己在本轮 `later_item` 里选的时刻（`PARK_UNTIL_CHOSEN_TIME`）。
- 选择复用既有 Decision 输出字段 `reevaluation_hint_ref`：`PAUSE` 时它可以且只能填写本轮
  `DecisionContextSnapshot.known_waiting_basis_ref`（AG-1 用 allow-list 校验，其他一律
  `PAUSE_CONDITION_NOT_OFFERED`）。没有新增 Decision key、condition kind 或持久字段。
- **无条件 PAUSE 仍然合法**：不填 hint 就是原来的 PAUSE，这次采纳不建 condition、不发
  OPEN eligibility，因此当前这一轮不会出现 `RESUME_ELIGIBLE`。这不声称未来任何合法的显式
  操作都不可能生成资格。没有任何默认定时器，也没有从 `short_rationale`/数字里猜条件。

**2. PAUSE 采纳后由 LR-4 在同一个 store 里绑定条件，不再动 Activity。**

- AG-2 在真正执行完 LR-2 PAUSE 之后调用新
  `WaitingCoordinator.bind_pause_condition`：要求 Activity 此刻确实是 `PAUSED` 且
  `revision == expected_revision`（即 PAUSE 刚产生的那一版），复用既有 `ResumeCondition`
  aggregate、condition store、lease/capability、evaluator 和资格生成器。
- 条件带一个既有的自由 `parameters` 标记 `pause_bound=true`，并生成自己的 `wep:` 身份。
  这是**条件宿主谓词**的区分，不是新 schema：`condition_host_is_live` 让 WAITING 条件仍只认
  WAITING 且 revision 一致，PAUSE 条件只认 PAUSED 且 revision 一致。
- PAUSE 不释放前台、不改 Activity 记录、不建 agenda item；`release_foreground`/`schedule_agenda`
  即使出现在 spec 里也被忽略。前台的占用规则与原来完全一样。
- 两个 owner 的两次写入不原子。若 LR-2 的 PAUSE 已提交而 LR-4 的条件写入失败，AG-2 允许
  **同一个已提交 Decision** 在重试时完成唯一绑定：它先用 LR-2 自己的幂等重放证明这次暂停
  确属本 Decision，不再执行任何状态转换，只补写那一条 condition。恢复只认
  `AUTHORIZED` 预授权、当前 `PAUSED`、以及 transition 上 Decision/Adoption 全匹配；无条件
  PAUSE、已离开 PAUSED 的 Activity 或别的 Decision 都不走这条路径，也不会产生资格。
- 显式条件 PAUSE 遇到 `SAFE_BOUNDARY`（PAUSE 只会登记为待处理、Activity 仍 ACTIVE）时，
  在调用任何 owner 写口之前整体拒绝 `PAUSE_CONDITION_REQUIRES_IMMEDIATE_PAUSE`；坏 spec、
  未提供的 spec 或与 hint 不匹配的 spec 同样在副作用前拒绝。

**3. 维护、重启与资格仍走原链，只是宿主谓词多了一档。**

- `maintain_waiting` 继续用本轮同一份 owner 读重查 PENDING 条件；作品 head 真变了才交一个事件，
  身体要新一次 FRESH 读且证据真的动了一格才交事件。LR-4 仍是唯一判 SATISFIED/OPEN 的 owner。
- reconcile 不再把「刚绑定的 PAUSED 条件」当 STALE；但 Activity 换状态（如被 ABANDON）或
  revision 变了仍照旧失效。重启后条件仍在同一个 store，旧事件重复投递也不会重复发资格。
- 资格 OPEN 之后 Activity 仍是 PAUSED；要回来只能靠一轮**新鲜 Decision** 自己选 `RESUME`，
  选 `NO_ACTION` 就什么都不发生。RESUME 仍走 B1 的既有入口与 LR-2 状态转换，消费该资格。

**4. WAITING 与既有词表原样。**

`enter_waiting_with_condition`、WAITING 的 condition/agenda/episode 清理、`release_foreground`、
LR-2 的 PAUSE/ABANDON 规则、候选供给、AG-0 来源准入（tombstone/不可读/展示后复查）全部未改。
本决定只增加「PAUSED 也能作为一个条件宿主」这一档，不把 `status == WAITING` 机械放宽。

## Alternatives considered

1. **PAUSED 自动视为可恢复，或在 PAUSE 时直接发 OPEN。**
   最强理由：实现最短，能立刻解开遗留实例；「暂停」看起来本来就意味着以后会回来。
   否决：设计原文只说「各自恢复条件满足后」回来，没有说 PAUSED 本身等于条件满足；直接发资格
   等于代码替她宣布恢复，也绕过了 owner 证明。B1 已明确拒绝过无资格恢复。

2. **用计时器：暂停 N 分钟就算条件满足。**
   最强理由：模型天然懂、实现省、报告好读。
   否决：时间不是条件，这正是 pacing 笔记已经否决的路；「自选时间」只能由她自己在本轮明确指出
   （park），不能是系统默认。

3. **新增 `PAUSE_CONDITION` condition kind、新字段或第二个资格 store。**
   最强理由：语义最干净，WAITING 与 PAUSED 两条链彻底分开。
   否决：本轮批准范围是「复用既有词表/字段/spec」，新增 kind/schema/owner 会扩协议；现有
   `ResumeCondition` + `parameters` 已经足够表达「同一条依赖，宿主状态不同」。

4. **把 PAUSE 改写成 WAIT（或让 PAUSE 复用 WAIT 的转场）。**
   最强理由：LR-4 的维护/资格链一行都不用改。
   否决：PAUSE 与 WAIT 的前台语义不同（PAUSED 占前台，WAIT 可以释放），把 PAUSE 偷换成 WAIT
   会改变既有状态机和用户可见行为。

5. **手工构造 OPEN eligibility 解卡，或让 reconcile 保留所有 PAUSED 上的开放记录。**
   最强理由：可立即让现场实例恢复。
   否决：任务与 B1 都禁止手造 OPEN 来冒充生成器；无条件放宽会把过期/错 revision/错 episode
   的资格一起放行。

6. **只在 prompt 里说明「可以选条件 PAUSE」，业务不接线。**
   最强理由：改动最小。
   否决：模型给了 hint 也没有 owner 绑定，条件永远不会被证明，等于把不可达包装成选项。

## Consequences

- **收益**：PAUSE 第一次有了可核对的回来条件，而且条件来源、宿主 revision、幂等与资格仍由既有
  owner 决定；无条件 PAUSE 的语义没有被偷改；WAITING 完全不变。维护和重启不需要新调度器。
- **代价与已知边界**：
  - 条件 PAUSE 只支持 owner 能立即执行 PAUSE 的情形。若当前中断性是
    `SAFE_BOUNDARY`（PAUSE 只登记为待处理、Activity 仍是 ACTIVE），整个采纳会具名拒绝
    `PAUSE_CONDITION_REQUIRES_IMMEDIATE_PAUSE`，不会留下「有条件却永不绑定」的半套状态。
  - PAUSE 与 condition 是跨 owner 的两次写入，不存在共同事务。LR-2 已提交、LR-4 写失败时
    Activity 会先变成 PAUSED；这一状态由上面「同一 Decision 重试补绑」恢复，而不是回滚。
    若 Activity 在重试前已经离开 PAUSED，恢复不适用，也不会凭空重建条件；是否出现这类
    更晚的状态变化由既有 owner 规则决定，本决定不新增回滚或迁移。
  - 无条件 PAUSE 的当前语义是「这次采纳不生成资格」。它不保证后续任何合法的显式操作
    （例如她在另一轮明确选择一个条件）都不能产生资格。
  - 自选时间条件仍要求同一答案里的 `later_item.park_current=true`；没有它会被 spec 拒绝，
    不会补一个默认时刻。
  - 身体条件只支持既有 `BODY_CAPACITY_RESTED` 词表；未知/不新鲜/不同身体都不会满足。
  - 来源准入沿用既有 AG-0 合同：Artifact 目标按 subject 可读性（tombstone/删除/不可读）。
    撤销写 grant 不会让 subject 失去读权；非 Artifact 目标的来源撤权仍未覆盖。
  - 方法 RETIRED/退休不是 PAUSE 条件词表的一部分，本决定未新增该维度：没有可用权威时不虚构
    退休字段或测试，如实记为不适用。
  - 测试与冒烟的 Decision/内容都是脚本替身，条件 source 是真实 Supply owner 的 head；
    这**不证明**真实模型会自然选择条件 PAUSE，也不证明 G1、24h 或长时恢复。
- 出现以下信号应重访：出现第三种条件词表；owner 需要「延迟到安全边界再绑条件」；或真实模型
  在无 hint 与有 hint 之间表现出系统性误解。

## Verification

- 新测试 `tests/test_paused_explicit_condition.py`（18 例，真实 LR-2/LR-4 owner、隔离临时根）：
  真实 PAUSE 后绑定且不二次转换、同 key 幂等、错 revision/无 Decision/无 Adoption 拒绝、
  ACTIVE/WAITING 拒绝绑定、真实新 head 只发一个 OPEN、重复事件不重发、同根新 coordinator 续接、
  terminal 取消条件且不发资格、自选时间只在显式时刻满足、不可读目标来源不供候选、
  WAITING 条件仍走原路径、AG-1 只接受本轮 offered basis。
  其中 3 例走真实 AG-2：`SAFE_BOUNDARY` 与坏/未提供 spec 都在任何 owner 写口之前拒绝，
  Activity 状态与 revision 不变、0 condition、0 eligibility；在 `before_condition_commit`
  注入一次失败后，同一 Decision 的重试以 LR-2 幂等重放为判据补写唯一绑定，暂停不被重放、
  revision 不变、不产生资格，再重试为幂等重放且仍只有一条 condition。
- `scripts/paused_explicit_condition_smoke.py`（`SMOKE_OK`）：真实 Supply owner + 正式
  `build_production_life_runtime`/`assemble_autonomous_life`；START → `no_change` →
  正式 Decision `PAUSE` + hint → AG-1 校验 → AG-2 真 PAUSE → LR-4 绑定 → owner 真提交新版本 →
  `maintain_waiting` 零模型满足并发一个 OPEN → 新鲜 `NO_ACTION` 保持 PAUSED/OPEN →
  再一次 owner 新版本后新鲜 `RESUME` 同一 Activity 且资格 CONSUMED。
  同一个冒烟里另起一个隔离实例跑无条件 PAUSE 负控：本轮确实提供了 condition，脚本 Decision
  不点名它；正式 `evaluate_once` 采纳 PAUSE 后 0 condition、0 eligibility，owner 真提交新
  head 后 `maintain_waiting` 仍无 satisfied/eligibility 且 0 llm，下一轮候选不含
  `RESUME_ELIGIBLE`/`RESUME`，Activity 保持 PAUSED。两条链互不共享实例或 runtime。
- 相关回归：`tests.test_paused_eligible_alignment` + `tests.test_life_wait_resume_v04` +
  `tests.test_parked_candidate_supply` 共 62 例通过（WSL `/tmp/rc104-venv`）。
- 新链自身实际覆盖：条件绑定、owner 满足、候选供给、显式 RESUME、无条件负控、AG-2 的
  副作用前拒绝与同 Decision 补绑恢复。**不覆盖**：真实撤权（测试里的来源拒绝是替身
  `target_read` 拒读，不是 store/owner 撤销）；非 Artifact 目标的来源撤权；退休/方法维度
  没有可用权威，未虚构；真实模型自然采用、G1、24h 与长时恢复。
- 相关决定：[PAUSED 资格对齐](../bug-fix/2026-10-23-paused-resume-eligibility-alignment.md)、
  [有效搁置供给](2026-10-24-eligible-parked-candidate-supply.md)、
  [身体断点与回来](2026-11-15-v04-life-pacing-breakpoint-and-resume.md)。
- **2026-10-24 后续规则（只取代前台语义）**：用户随后批准 PAUSE 释放执行槽，允许多件挂起、
  单件执行，后台恢复不得抢占。本笔记 Decision 第 2 条“PAUSE 不释放前台、不改 Activity 记录”
  中的**前台部分**被新规则取代：一次成功 PAUSE 现在写 `foreground_released` 并清空
  `foreground_activity_ref`；条件仍由 LR-4 独立 store 持有，绑定/维护/资格生成/显式 RESUME
  路径不变。跨 owner 补绑的恢复目标改为 adoption 自己的 `authorized_activity_ref`，不再从
  当前前台猜。旧 PAUSED 指针不迁移。见
  [PAUSE 释放执行槽](2026-10-24-paused-background-single-execution.md)。
