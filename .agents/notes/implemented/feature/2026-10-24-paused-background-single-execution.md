# Agent Note: PAUSE 释放执行槽 —— 多件挂起、单件执行、后台恢复不抢占

Status: implemented

## Problem

真实短日 i/j 都出现过同一个状态：她主动 `PAUSE` 了阅读 Activity，随后 `NO_ACTION`。
上一单已经让 PAUSE 能显式绑定一个 owner 可核对的条件（
[显式条件 PAUSE](2026-10-24-paused-explicit-resume-condition.md)），但那次决定明确保留了
旧前台语义：**PAUSED 仍占着 `foreground_activity_ref`**。于是：

- 原活动悬着时，任何新 Activity 的 `START` 都必被 LR-2 拒绝；
- 她的其它真实机会（阅读、World、自己的作品）在 AG-1 合法集里只剩 `DEFER`；
- 只修“怎么回来”不解决“悬着的时候还能不能做别的”。

只读核对见
[20261024-paused-foreground-audit-result.md](../../../worker-runs/20261024-paused-foreground-audit-result.md)。
用户 2026-10-24 直接批准改变这条产品语义：“PAUSE 时释放前台，允许同时挂起多件事，
但同一时刻只有一件执行。挂起活动保留显式等待条件；条件满足后仍自行决定是否回去。
定义后台挂起恢复时若前台正有活动的冲突处理，不得抢占、不得自动中断。”

本笔记记录的是**新规则**，不是“旧规则本来就这样”。旧笔记记录的是当时的决定，不能被
事后改写；这里只说明它被什么取代。

## Decision

**1. 一次成功的 PAUSE 释放执行槽，但不动 Activity 自己的记录。**

`ActivityCommandService._mutate_existing_activity` 在 PAUSE 真正提交时：

- 给 PAUSED 记录写既有的 `foreground_released = True`（WAIT 释放时已经在用同一个字段，
  不是新 schema）；
- 只有当 `foreground_activity_ref == 这个 Activity` 时把它清空；
- 所有停点、`paused_at`、`source_refs`、`last_transition_ref`、revision 与 LR-4 条件原样保留。
  条件仍在既有 `ResumeCondition` store 里，PAUSE-bound 标记不变。

`SAFE_BOUNDARY` 的 PAUSE 只登记待处理中断、Activity 仍是 ACTIVE，因此没有“释放”可做；
ATOMIC 前台由 AG-1/AG-2 的既有门拒绝，也不会提前释放。错 revision 在 LR-2 的 CAS 处失败，
前台不变。

canonical 校验和确定性 replay 同步认识这个字段：`PAUSED + foreground_released` 的
OPEN Activity 不再计入 `fg_bound_open_ids`，所以多个 PAUSED 可以共存，`foreground` 为
`None` 是合法状态。`ACTIVE` 仍然只有至多一个，且当它存在时必须就是 foreground。

**2. 前台空时，新鲜 Decision 可以 START 另一件事。**

AG-1 的判据本来就是“foreground 引用是否还在”（`foreground_holds_occupancy`）。
新 PAUSE 把引用清掉后，它自然对其它候选重新给出 `START` / 另一活动的 `RESUME`；
AG-0 的 `CURRENT_ACTIVITY` 源只读 `foreground_activity_ref`，因此一个新 PAUSED 活动
不会再被投影成“当前执行活动”。它的回来入口是 `RESUME_ELIGIBLE` 源 + LR-4 资格。
条件继续由既有 `maintain_waiting` 维护，同源 revision/CAS/幂等/expiry/失效门没有放宽。

**3. 后台恢复不是抢占：有另一件在执行就拒绝，资格不消费。**

当 `foreground` 指向另一件 ACTIVE 活动时：

- AG-0 的 `RESUME_ELIGIBLE` 源仍按原有前台冲突规则不提供该候选；
- AG-1 不对该候选给 `RESUME`；
- AG-2 的 RESUME 分支**不再先 pause 当前活动**（旧代码会 pause 一个 ACTIVE 前台，
  然后仍因前台引用未真正释放而失败，留下真实副作用）；现在在任何授权或写口之前
  具名拒绝 `FOREGROUND_CONFLICT_ON_RESUME`；
- LR-4 的 `explicit_resume_from_eligibility` 先 reconcile 再走 LR-2；LR-2 自己的前台检查
  零副作用拒绝，资格既没有被标 `STALE`，也没有被 `CONSUMED`；
- 同一 Decision 重试不会绕过当前冲突：冲突是状态事实，不是过期。
- WAITING 前台的 `allow_background_waiting` 特例原样保留，没有扩成 PAUSED。

前台空后，同一个 LR-4 → LR-2 路径把旧 Activity 恢复为 ACTIVE，消费资格，且不触碰
另一件已挂起的活动。

**4. 跨 owner 的条件补绑认原挂起 Activity。**

条件 PAUSE 是 LR-2 暂停 + LR-4 绑定两次写。绑定失败后同一 Decision 可以补绑；新规则下
PAUSE 已经释放前台，补绑前可能有另一件 Activity 占用了槽位。补绑的恢复目标因此取自
该 adoption 自己的 `authorized_activity_ref`，**不再从当前 foreground 猜对象**，也
不会为了补绑去暂停另一个 Activity。条件满足只产生 OPEN 资格，不自动 RESUME；新的
`NO_ACTION` 保持挂起和资格。周期 fingerprint/quiet 仍用既有事实，不改阈值或调度。

**5. 旧记录兼容，不做隐式迁移。**

旧版本写下的 PAUSED 记录可能仍让 `foreground_activity_ref` 指向自己，且没有
`foreground_released` 标记。本决定**不批量改库、不在读取时偷偷写回**：

- canonical 校验继续接受这种旧状态（foreground 指向它、且它是唯一开放绑定）；
- AG-1、AG-2 和 LR-2 仍把它当成占用槽位的旧指针，旧实例的 START/RESUME 行为不变；
- 它自己的 RESUME 仍可走 LR-4 + LR-2；它不会被自动迁移或自动恢复。

从新 PAUSE 开始产生的记录才带释放标记，因此“旧指针”与“新挂起”是两种可区分的状态。

**6. PAUSED 的终端出口在状态机层保持，但安全门不动。**

新规则前，PAUSED 占前台，LR-2 的 `COMPLETE/ABANDON/CANCEL/EXPIRE` 因前台匹配而可执行；
释放后如果继续要求“必须是 foreground”，这些底层能力会凭空消失。因此 LR-2 对
`PAUSED + foreground_released` 的 terminal transitions 仍允许（与 WAITING 释放后的既有
规则并列）。但 AG-2 的 `PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE` **原样保留**：正式链
仍然不能 ABANDON 一个 PAUSED Activity，因为“没有未解决副作用/承诺”仍无法从既有 owner
事实证明。`CHECKPOINT/PROGRESS` 对后台 PAUSED 仍然拒绝：没有执行就不该在背后推进进度。

## Alternatives considered

1. **允许多个前台（多执行）。**
   最强理由：最直接满足“同时做几件事”，不用区分挂起与执行。
   否决：用户批准的是“多件挂起、单件执行”，不是多执行。单前台是既有承重墙，改它
   会牵动注意力、中断、Atomic、候选合法集和所有 owner 的假设；本单不拆。

2. **PAUSE 时自动 ABANDON，或条件满足时自动 RESUME。**
   最强理由：实现最短，模型不需要再做一次选择，也不会长期悬着。
   否决：这是替她做决定。PAUSE 是“先放下”，不是“放弃”；条件满足只说明可以回来，
   回不回来仍由新鲜 Decision 选。也与既有“不自动恢复/不自动中断”一致。

3. **后台 RESUME 与前台冲突时，先把当前活动 pause 掉再恢复。**
   最强理由：模型想切换是正常的，owner 直接腾位最省事。
   否决：**抢占**。用户明确要求“不得抢占、不得自动中断”。旧代码正是这么做的，而且在
   PAUSE 仍占前台时命令还会失败，留下“暂停了别人、自己没恢复”的真实副作用。

4. **冲突时把资格标 `STALE` 或直接 `CONSUMED`。**
   最强理由：资格与前台不再匹配，看起来像是失效；消费掉可以防止反复尝试。
   否决：冲突是“现在不能执行”，不是“资格失效”。标 STALE 会抹掉她之后回来的机会，
   消费则是把没发生的恢复记成发生。两者都篡改 owner 事实。

5. **只改 AG-1 允许第二个 START，底层不动。**
   最强理由：改动最小，模型能看到更多选项。
   否决：LR-2 仍会拒绝，AG-1 就又在提供必被拒的选项，正是上一层决策合法性笔记要修的
   问题。必须同时改 owner 的槽位语义，而不是只改展示。

6. **读取时把旧 PAUSED 指针当成已释放，或批量迁移旧记录。**
   最强理由：让旧实例立刻获得新能力，代码路径也统一。
   否决：读取变写会偷偷改变历史状态；批量迁移需要动旧 root/旧数据，而且无法核对
   旧记录的语义。兼容边界明确保留，等旧实例自己走 RESUME 或用户另行决定。

7. **把 `CHECKPOINT/PROGRESS` 也放给后台 PAUSED。**
   最强理由：看起来与 WAITING 释放后的规则一致，能减少“后台活动不能写进度”的惊讶。
   否决：WAITING 是等待依赖，PAUSED 是暂停执行；后台没有执行却写进度，会把没有发生的
   步骤记成发生。保留拒绝，等有真实 owner 需求再单独讨论。

## Consequences

- **收益**：一次 PAUSE 之后，执行槽真的空出来；她可以在原活动悬着时开始另一件真实的事，
  两件（或多件）挂起活动各自保留条件、停点与来源，回来仍走同一条 LR-4 资格 + LR-2 RESUME。
  条件 PAUSE 的跨 owner 补绑不再依赖“前台还空着”，也不再把恢复目标错认成新前台。
- **代价与边界**：
  - 旧 PAUSED 指针不迁移。旧实例仍表现为“PAUSED 占前台”，行为与升级前一致；这是有意
    保留的兼容边界，不是遗漏。
  - 后台 PAUSED 没有 `WAIT`、`CHECKPOINT`、`PROGRESS` 入口；它的回来路径只有 RESUME，
    终止路径只有状态机层，正式 ABANDON 仍被 AG-2 的安全门扣留。
  - 前台冲突的拒绝发生在 AG-2/LR-4/LR-2 三层，任何一层都零副作用；但“展示后到采纳前”
    的竞态仍由既有 freshness/owner CAS 处理，本单不新增原子性保证。
  - 资格、条件、候选来源准入全部沿用既有合同：Artifact 删除/tombstone/不可读不供候选，
    撤写 grant 不等于读来源撤权，非 Artifact 来源撤权仍未覆盖。
  - 没有真实模型、没有真实短日、没有 G1 或 24h 证据；Decision 和内容仍是脚本替身。
  - 本决定不改变默认 gate、预算、lease、安全时钟，也不启动任何旧实例。
- 出现以下信号应重访：真实模型在有槽位时仍系统性不选新活动；旧 PAUSED 指针造成实际
  阻塞且用户要求迁移；或后台 PAUSED 需要真正的进度/终止规则。

## Verification

- 新测试 `tests/test_paused_background_single_execution.py`（11 例，真实 LR-2/LR-4/AG-2
  owner，隔离临时根）：PAUSE 释放与字段保存；两件挂起共存；条件 reconcile 与同根重启；
  后台 RESUME 在另一活动执行时零副作用拒绝且资格保持 OPEN；前台空后同一 Activity 恢复；
  无条件 PAUSE 零条件/零资格；错 revision 不释放；AG-1 在槽位空/被占/旧 PAUSED 指针下的
  合法集；SAFE_BOUNDARY 与 ATOMIC 不提前释放；补绑认原挂起 Activity 而不是新前台；
  AG-2 的 RESUME 在抢占前拒绝。
- 新冒烟 `scripts/paused_background_single_execution_smoke.py`（`SMOKE_OK`）：真实 Supply
  owner 创建作品甲/乙；正式 production runtime/assembly/evaluate_once/AG-1/AG-2/LR-2/LR-4；
  START 甲 → 条件 PAUSE 释放 → owner 真提交新 head → LR-4 发一个 OPEN 资格 → START 乙 →
  LR-4/LR-2 前台冲突负控（乙不被暂停、资格不被消费、revision 不变）→ PAUSE 乙释放 →
  NO_ACTION 保持两件挂起 → owner 再提交甲 head → RESUME 甲（同 id，乙仍 PAUSED，资格
  CONSUMED）。每个阶段断言 canonical 至多一个 ACTIVE 且 foreground 正确。
- 相关回归：`tests.test_paused_explicit_condition`、`tests.test_paused_eligible_alignment`、
  `tests.test_parked_candidate_supply`、`tests.test_life_wait_resume_v04`、
  `tests.test_day_life_decision_legality` 共 94 例通过（C 命令）。
- 未覆盖：真实模型自然选择、真实短日、G1、24h；旧 root 的迁移；PAUSED 的后台进度/终止；
  真实撤权与非 Artifact 来源撤权。
- 相关决定：[显式条件 PAUSE](2026-10-24-paused-explicit-resume-condition.md)、
  [AG-1 只提供 owner 会接受的决策](../architecture/2026-10-26-v04-day-life-decision-legality.md)、
  [PAUSED 资格对齐](../bug-fix/2026-10-23-paused-resume-eligibility-alignment.md)、
  [有效搁置候选供给](2026-10-24-eligible-parked-candidate-supply.md)。

## 2026-10-30 修订：提示文案对齐，不改任何行为

`AutonomousLifeCycle.pause_condition_view` 的描述文本此前仍写“PAUSE keeps the foreground”，
与上面第 1 条的“成功 PAUSE 释放执行槽”相反。本次只改这段描述：

- docstring 与可绑条件分支的 `how` 改为：成功 PAUSE 释放执行槽，条件满足只给 OPEN 资格、
  不自动恢复，回来仍由新的 Decision 选 RESUME，后台 RESUME 不抢占另一件前台活动；
- 描述字段 `keeps_foreground` 由 `True` 改为 `False`。该字段只被
  `agency_cognition_adapter._bounded_pause_condition` 白名单透传进提示，没有正式逻辑读取它；
  字段名与结构不变。

不改条件、资格、排序、TTL、owner、恢复入口或默认 gate；没有 basis 时的
`available: false` / `NO_OFFERED_CONDITION` 文案原样保留，不新增“永远回不来”的断言。

边界：这是提示输入的变化，模型看到的条件块文字与旧版不同，因此与本次之前的历史运行
（含 A 的恢复观察）不构成逐字可比的提示输入；它不证明会自然返回、不改变“条件满足只给资格”
的既有语义，也不解决 A 未返回的问题。

## 2026-10-30 修订二：让「PAUSED 时放弃被扣留」这一代价进入提示材料

**事实依据**：2026-10-30 的真实离线实验已逐跳跑通 ACTIVE 阶段的正式放弃链（AG-1 合法集 →
AG-2 授权 → LR-2 `ABANDONED`），见
[active-abandon-reachable-20261030.md](../../../../docs/engineering/active-abandon-reachable-20261030.md)；
而 PAUSED 后台的正式 `ABANDON` 仍被 `PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE` 扣留（上面第 6 条）。
同一轮对既有 PAUSE 提示材料的有限核查**没有找到**任何一句明确说明“PAUSED 后放弃不可用”，
但这只说明材料未写明，不能推断模型不知道或不理解。

**本次改动**：`pause_condition_view` 的可见材料是「新增事实句 + 恢复被240截断的完整规则尾段」，不是只加一句；其中新增的系统确知事实是——
「While the Activity is PAUSED, ABANDON is not available through the formal decision path;
if you want to abandon it, make that choice while it is still ACTIVE.」
有 basis 与无 basis 两个分支都写；语气是客观陈述，不劝阻暂停。该句限定当前 PAUSED 状态，
不写成永久失去（Activity 恢复成 ACTIVE 后正式路径仍可提供 ABANDON）。
`agency_cognition_adapter._bounded_pause_condition` 的白名单键集合不变，只把 `how` 的截断
上限从 240 提到 680 字符，避免这句（以及既有的 park 说明）在投影时被从中间截掉；
240处原来还截掉了通用尾段：OPEN资格、不自动恢复、新Decision自选RESUME与后台不抢占；
放宽后这些既有说明也新增可见，不只是新事实句和park说明。逐字差额见
[有限材料差额](../../../../docs/engineering/pause-cost-material-delta-20261030.md)。

**静态系统提示变更（独立项）**：原先称PAUSE“保留前台”的静态系统提示改为“释放执行槽”。
这不是注释；使用该正式adapter构造请求时，每轮共有的基础说明都已改变，影响不局限于暂停视图。

**未改变**：AG-1 合法集、AG-2/LR-2/LR-4、条件、资格、状态机、TTL、排序、owner 合同、
默认 gate 与预算均未动；没有新增键、schema 或字段。红线内容（“只有你能解”“条件永不满
足”“等自己的作品所以等不到”）没有进入模型材料。

**边界**：这是**模型输入的变化，且每轮基础材料同时变化**，不只是条件块多一句话。
与 a/b/c 三批真实运行使用的提示输入不同，新观测不能当同输入/同策略横向比较，
多因素同时变化，不能把行为差异归因于放弃提醒或任一单独变动：输入变了，行为是否变、为什么变，未知。
改前240字符文本仅按本单变动重建，没有独立前快照，不是历史现场；当前park拼接的`ACTIVE.;`
是已知文案瑕疵，原样保留，本单不扩范围修正。它只让代价可见，不保证她会因此继续做，也不恢复 r23 里已经
不再合法的 CONTINUE；不声称解决了原 A 的返回问题，也不构成 G1 证据。新增测试
`tests/test_pause_abandon_cost_copy.py` 只证明该字符串能通过正式 adapter 白名单投影，
且正式 AG-1 对最小 PAUSED 形状仍不给 ABANDON（ACTIVE 对照仍给），不证明完整最终 prompt
已发送或模型已理解。

## 2026-11-01 测试夹具对齐（不改行为）

`tests/test_paused_abandon_existing_path.py` 原先假设「PAUSED 仍占前台，并被 AG-0 投影成
CONTINUE_CURRENT」。PAUSE 释放执行槽之后这不再成立，夹具改为：断言 PAUSED 记录带
`foreground_released`、`foreground_activity_ref is None`、AG-0 投影 0 个候选；AG-1 的
`PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE` 与 AG-2 的同类拒绝仍由「伪造 ABANDON 命名该
PAUSED Activity」打到（两处都是状态判定，与前台指针无关）。没有改条件、资格、排序、owner、
默认 gate。共同活动路径在 PAUSE→WAIT 顺序下被 LR-2 拒绝属于独立的生产接线问题，不在本修订
内处理，也未用测试替身绕开。

## 2026-11-01 修订二：上句「生产接线问题」的判断被纠正（四红是旧测试入口）

上面的判断只对**旧入口** `FiniteCoPresence.begin` 成立，它不是生产路径：生产由
`life_social_contacts` 调用 `begin_from_decision`——Decision 先行，前台交接是采纳自己的
第一条命令（LR-4 WAITING + release，见
[来访决定先行](2026-11-21-v04-social-life-local-contact-and-co-presence.md)）。旧 `begin`
的「先 LR-3 PAUSE、再 WAIT」在新前台语义下必被 LR-2 拒（PAUSE 已释放前台），这是旧入口与
新规则的组合，不是正式共处链的缺陷。

`tests/test_life_v1_integration.py` 的四个红（共同时间闭环、读不消费资格、只记录实际消费、
未采纳不报 started）此前都直接走旧 `begin`，现已改走正式 `begin_from_decision`（Decision/
内容仍为脚本替身）。同时按既有规则对齐测试侧事实，未改任何生产行为：

- 每个自然轮显式给 `observed_at`，与邀请、end 事件同一时间轴；邀请声明 2 小时窗口，使有界
  等待条件覆盖整段测试时间线（默认 15 分钟窗口短于测试的 end 时刻）。
- 一件 Activity 真实占着唯一前台时，另一件资格不再被要求「仍可选 RESUME」——按不抢占规则
  断言它保持 OPEN 但不供给，而不是放宽 AG-0。

2026-11-01 同一功能返修（上一版测试未被验收的两点）：

- 上一版在 DEFER 后显式 `reconsideration.release(key=…)` 再继续，是把**强制解除静默**当成
  自然重新考虑，违反产品「同状态不再问」的规则。此处撤销两处 release：DEFER 之后下一同状态
  轮断言 `QUIET`/`llm_calls == 0`、资格仍 `OPEN`、水位未记录采纳；要验「之后仍可消费」改为
  另起独立新根的正常路径（从未 DEFER，end → 一次 RESUME 消费，第二次不再是 RESUME），两条
  分开证明，不拼成同一条自然轨迹。
- 上一版用 `NO_ACTION`（Decision 根本没选共同时间）冒充「采纳失败」，与原负控不等价。现拆成
  两条：`NO_ACTION` 仍单独作为「Decision 未选择即不报 started、私人状态原样」的负控；真正的
  采纳失败用测试侧可控的 LR-2 start 命令故障，在**已提交 START Decision 之后、采纳边界**触发
  ——产品既有的 stranded 边界：`HANDOFF_STRANDED` / `JOINT_START_FAILED_AFTER_HANDOFF`，
  私人 Activity 被 LR-4 置为 WAITING、交接释放为真实 OPEN 资格、session 为 `PARKED_NO_JOINT`，
  不报 started、无 joint Activity。生产源码零改动，只测试端命令端口故障。

验收：WSL `owned_test_runner --files test_life_v1_integration.py` 全例 OK、exit 0；本单
`components/` 零改动。
