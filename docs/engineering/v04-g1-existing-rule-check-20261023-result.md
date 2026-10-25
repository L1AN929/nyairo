# G1 现有规则对齐核查：区分内部未接通与新增产品行为（2026-10-23）

结论先说：**三类问题必须分开，不能统一叫"契约问题"，也不能统一叫"内部缺陷"。**

1. **B1 是内部实现收窄**：AG-1 已经给"前台 PAUSED + OPEN eligibility"发 RESUME 和 ABANDON，LR-2 也允许 PAUSED 恢复/放弃，只有 LR-4 的显式 RESUME 入口硬要求目标为 WAITING。设计状态机写的是"PAUSED → READY（满足各自恢复条件）"，所以"PAUSED 一旦有恢复条件就能回来"在规范层是成立的，LR-4 那一条 WAITING 断言是收窄，不是规范要求。**但修好它对本案遗留活动无效**：该活动没有 eligibility，没有 World 可识别目标，没有 completion contract。
2. **B2 是新增可选行为，需要本人拍板**：给"PAUSED 且无 eligibility"补自我 RESUME 候选，直接绕过资格，与 12 号 AG2 合同和既有 note 的否决理由冲突；补 ABANDON 候选虽然符合设计状态机的"PAUSED → ABANDONED（owner 判定）"，但它扩大了模型可见选择集合，属于产品规则，不是监工能顺手补的接缝。
3. **B3 也是新增规则**：设计有"暂停必须有恢复条件"的原则（SRC-0233），但**没有**规定 PAUSE 采纳时必须绑哪一种机器可核对的条件，也没有授权事后把条件当成已满足。既有 note 还明确否决过"给 PAUSED 发 WAIT 基础"这条捷径。

因此：**本次无法在无行为决定的前提下让遗留实例恢复并完成 G1。** 能立即做的只有 B1 的窄对齐，而且它不制造任何可运行路径。

## 规范对照

### 一、规范来源（只读命中段，未通读总纲）

按权威层级从高到低记为：**核心设计正文（原文摘录）> 工程基线/宿主映射（12 号，明确标注"待实施的历史合同"）> 实现 note**。设计里"0.4"不是版本号，本核查按功能对。

- **SRC-0719（L11740–L11779，原文正文 Q8 Activity 状态机）**，L10–L13 原文：
  ```text
  ACTIVE → WAITING_EXTERNAL / BLOCKED / PAUSED
  WAITING_EXTERNAL / BLOCKED / PAUSED → READY（满足各自恢复条件）
  READY / ACTIVE / WAITING_EXTERNAL / BLOCKED / PAUSED
    → COMPLETED / FAILED / CANCELLED / ABANDONED（相应 owner 判定）
  ```
  L16 原文：「PAUSED 是明确暂缓」「ABANDONED 是主动停止追求。终态默认不复活，重做创建 successor Activity。」
  要点：**PAUSED 的出口有两条**——满足自身恢复条件回 READY，或由 owner 判定转 ABANDONED。**规范没有规定 PAUSED 的恢复条件必须是 WAITING 那套 eligibility。**

- **SRC-0007（L59–L80，0.3 当前硬不变量）**，L16 原文：「NO_ACTION 是合法结果；等待、暂停、放弃也是合法生活选择/transition intent。Activity 的 durable current state 仍以后期 R1 状态机为准。」
  要点：**放弃在规范层是合法生活选择**，不是异常路径。

- **SRC-0233（L4539–L4564，失败、挫折与恢复）**，L18 原文：「若前置条件缺失、工具停用或暂时无预算，暂停；……**暂停必须有恢复条件；无条件无限暂停是隐性堆积。**」
  要点：暂停**必须**带恢复条件，这是规范要求；但上下文是 Goal/Project review 的失败处理，**没有**规定条件在什么时刻、由谁、以什么形式绑定。

- **02-核心子系统与接口设计 L188**：START/CONTINUE/WAIT/PAUSE 是命令或转换意图，持久状态用九状态枚举。
- **04-设计决策与冲突记录 L31**：早期 START/WAIT 被同时当作状态的错误已纠正。
- **12-工程基线协调与真实宿主映射 L52**：「AG0当时接受 CURRENT_ACTIVITY、RESUME_ELIGIBLE、USER_REQUEST、COMMITMENT、EXPLICIT_GOAL、WORLD_OPPORTUNITY」，并注明来源是 `CHIYO_AG2_HANDOFF`，「这是一份**待实施的历史合同**，不是本次执行指令」。
- **12 号 L54**：「AG1输出 START、CONTINUE、PAUSE、WAIT、RESUME、ABANDON、DEFER、NO_ACTION。Decision不修改Activity；**Adoption重新检查 candidate、activity、resume eligibility**、constraint-relevant revision及中断状态。」
  要点：**"RESUME 必须带 eligibility"的出处是 12 号的 AG2 历史合同，不是设计正文。** 设计正文只要求"满足各自恢复条件"。

### 二、四项规范矩阵

| 待判事项 | 规范来源与原文 | 当前实现（具名行号） | 判定 |
|---|---|---|---|
| **① 当前 PAUSED 占前台** | SRC-0719 L10–L13：PAUSED 是非终态，可回 READY 或转 ABANDONED；未规定 PAUSED 释放前台 | `autonomous_life_cycle.py:5120-5131` 只有 WAITING 释放槽位；AG-1 `agency_decision_ag1.py:2930-2933` 同规则。PAUSED 期间 `candidate_sources_ag0.py:1155` 对 CURRENT_ACTIVITY 直接返回 `ACTIVITY_PAUSED_REQUIRES_RESUME_ELIGIBLE` | **规范未禁止 PAUSED 占位，实现的选择与规范兼容**；但"占位 + 无候选"的组合让模型只剩 DEFER，这是实现选择，不是规范要求 |
| **② RESUME 是否必须是 OPEN eligibility** | 设计正文**未找到**"OPEN eligibility"字样；12 号 L54 只写"Adoption 重新检查 resume eligibility" | AG-1 `agency_decision_ag1.py:3014-3035`：只对 `RESUME_ELIGIBLE` 且 OPEN、非空 eligibility 的候选开放 RESUME；AG-0 `candidate_sources_ag0.py:1266-1270` 只读 OPEN | **eligibility 是 12 号合同引入的实现形态，不是设计正文的硬性要求**；"必须 OPEN"在合同层成立，在设计层是空白。不能把它说成"设计规定"，也不能反过来当无关内部细节 |
| **③ WAITING 与 PAUSED 的恢复入口** | SRC-0719 L11：两者各自"满足各自恢复条件"回 READY，**没有共用同一入口的表述** | LR-2 `activity_continuity.py:2453` `allowed_from_statuses=(STATUS_PAUSED, STATUS_WAITING)`；LR-4 `activity_waiting_agenda.py:3040-3043` 显式 RESUME 却要求目标 `status == STATUS_WAITING`，PAUSED 抛 `EligibilityNotOpenError` | **实现把"各自恢复"压成了"只有 WAITING 能走 eligibility 恢复"**。这是实现收窄，属 B1 的核心事实 |
| **④ 主动放弃 / 主动等待是否允许** | SRC-0007 L16：等待、暂停、放弃是合法生活选择；SRC-0719 L12–L13：PAUSED 可转 ABANDONED（owner 判定）、可回 READY；SRC-0719 L18：UNKNOWN 后放弃等待可 ABANDONED | LR-2 `activity_continuity.py:2501/2548` 允许从 ACTIVE/PAUSED/WAITING ABANDON；但 AG-1 只在 `CURRENT_ACTIVITY && ACTIVE`（`agency_decision_ag1.py:2986-3012`）或"RESUME_ELIGIBLE 且候选就是当前前台"（`:3037-3045`）时给 ABANDON；WAIT 嵌在 CONTINUE 分支内，同样要求 ACTIVE（`:3005`） | **规范允许从 PAUSED 放弃/回 READY；实现在无 eligibility 的 PAUSED 上不提供 ABANDON 候选**。这是"规范允许、实现没接通"的典型，但接通它属于扩大可见选择，见 B2 |

补充两条不构成偏离、但必须写清的映射差异：

- SRC-0719 写 PAUSED → **READY**，LR-2 直接 PAUSED → **ACTIVE**（`activity_continuity.py:2453` 的 `to_status=STATUS_ACTIVE`）。这是"READY 与采纳合并"的层级映射，不是状态语义冲突；不能拿它当 B1 的额外理由。
- `activity_waiting_agenda.py:2253` 的 `enter_waiting_with_condition` 文档串写「Transition an **ACTIVE/PAUSED** Activity to WAITING」，与 AG-1 只在 ACTIVE 给 WAIT（`:2986-3012`）不一致。这是同一类"底层比决策层宽"的收窄，本身不等于上层应当开放。

## B1既有资格路径

**B1 的事实链（全部来自本次只读源码，未运行）：**

1. AG-1 对"前台 PAUSED + OPEN eligibility"已经给出 RESUME 和 ABANDON：`agency_decision_ag1.py:3014-3045`，Section 67 只要求 `candidate_kind=RESUME_ACTIVITY`、`source_kind=RESUME_ELIGIBLE`、`availability_status=OPEN`、`resume_eligibility_ref` 非空，**校验里没有排除 PAUSED**。
2. AG-0 的 RESUME_ELIGIBLE 来源校验允许 PAUSED：`candidate_sources_ag0.py:1367-1373` 只排除 terminal 和 ACTIVE。
3. LR-2 本身允许从 PAUSED 恢复：`activity_continuity.py:2453` `allowed_from_statuses=(STATUS_PAUSED, STATUS_WAITING)`。
4. 唯一拦路的是 LR-4 的显式 RESUME：`activity_waiting_agenda.py:3040-3043`，`act.get("status") != STATUS_WAITING` 就抛 `EligibilityNotOpenError`。
5. 测试已经把这个"AG-1 给、owner 收"当既有事实断言：`tests/test_day_life_decision_legality.py:509-525`，同活动 PAUSED + 自有 eligibility 时 RESUME/ABANDON 都在合法集，别的活动则都不在。

**独立定性：这是"已有明确合法集合的实现收窄"，不是新增能力。**

- 依据一：设计 SRC-0719 L11 明确 PAUSED 可"满足各自恢复条件"回 READY，LR-2 的实现也支持。
- 依据二：AG-1 自己就是按"不给 owner 必拒的选项"设计的（2026-10-26 note 的原则），这里恰好反过来——给了必被拒的 RESUME。修 LR-4 是把 AG-1 已经展示给模型的选择变成可执行，**不新增候选类型、不新增来源、不放宽 eligibility 的开闭**。
- 依据三：不削弱任何安全门。要修的只是状态谓词 `{WAITING}` → `{WAITING, PAUSED}`；以下检查必须原样保留：eligibility 必须 `OPEN`（`:3029-3032`）、未过期（`:3034-3036`）、`activity_revision` 与当前 revision 相等（`:3043-3047`）、LR-2 的 CAS/幂等/前台冲突（`activity_continuity.py:2144-2175`）。前台是自己时 `fg_id == act_id` 不拦，这一条现状即如此。

**但必须写明三条限制，不能靠 B1 冒认进展：**

- **可达性未证且很可能极低。** 显式 RESUME 会把 eligibility 置为 `CONSUMED`（`activity_waiting_agenda.py:3067-3072`）；而 revision 相等门（`:3043-3047`）要求 eligibility 记录的 revision 与当前 PAUSED 活动一致。PAUSE 本身会推进 revision，所以"活动先 WAITING 拿到 OPEN eligibility、再变成 PAUSED、仍保留匹配 revision"这条链在现有词汇下**没有找到自然路径**。B1 更像是消除不一致，而不是打开一条真实通路。
- **对本遗留实例完全无效。** `actv_91acbd742551228b` 没有 eligibility（前述报告已核，本次未复测），修好 LR-4 也不会产生 eligibility。
- **不解决目标与完成契约。** World 目标事实（保存的是 `world_fact:` 前缀，进不了 step builder 的 `place:/object:/stay:/bodyact:/dailyact:` 命名空间）与 `completion_contracts_by_activity` 缺行，这两块只引用 `v04-g1-resume-path-20261023-result.md`，**本次未独立复现**。

所以：B1 可以修，但修完只能说"AG-1 与 LR-4 对 PAUSED 的口径一致了"，**不能说该活动能继续，更不能说 G1 有进展**。

## B2与B3行为边界

### B2a：给"PAUSED 且无 eligibility"补 ABANDON 候选

- **规范支持到什么程度**：SRC-0719 L12–L13 允许 `PAUSED → ABANDONED（相应 owner 判定）`；SRC-0007 L16 说放弃是合法生活选择；LR-2 `activity_continuity.py:2548` 也确实允许从 PAUSED 放弃。**状态转移在规范与 owner 两层都是既有的。**
- **缺的是什么**：决策层在 PAUSED 且无 eligibility 时**根本不生成任何可放弃的候选**（`candidate_sources_ag0.py:1155` 直接拒；Section 67 又要求 eligibility）。也就是说，缺的不是 owner 能力，是"什么条件下允许 chooser 提这个选项"这条规则。
- **定性**：**扩大模型可见选择集合的新增可选行为**，不是 B1 那种"已展示却被拒"的修 bug。规范给了状态机，没有给触发条件；而且 ABANDON 是终态、按 SRC-0719 Q10 还不解除承诺，属于产品可见后果。**需要本人拍板，不能以"设计允许 PAUSED→ABANDONED"一条就放行。**

### B2b：给"PAUSED 且无 eligibility"补自我 RESUME 候选

- **规范/合同明确反对**：12 号 L54 要求 Adoption 重新检查 resume eligibility；既有 note `2026-10-28-v04-wait-closeout-core.md:76-77` 明确写过「PAUSED 也发 WAIT 基础……等于绕过那条资格」，并因此否决。RESUME 绕过 eligibility 是同一性质的绕过，理由只会更强。
- **定性**：**新增行为且削弱资格门，必须本人决定。** peer 说的"内部修复"不适用于这一条。

### B3：PAUSE 采纳时自动绑定恢复条件

- **规范只给了原则，没给可执行规则**：SRC-0233 L18「暂停必须有恢复条件」；但没说条件由谁产生、以什么谓词表达、何时算满足。
- **既有决定已经挡过捷径**：`2026-10-28-v04-wait-closeout-core.md:76-77` 否决了"给 PAUSED 发 WAIT 基础、顺便让出前台"，理由是绕过资格且把"停下的事"降级。
- **不能虚构满足事件**：任何"PAUSE 时绑一个条件、事后再让它自动成立"的做法，都要先回答"这个条件对应什么真实事实"。本活动连 World 可识别目标都没有，找不到可绑的真实条件；凭空造一个等于把隐性堆积合法化。
- **定性**：**需要本人决定的产品规则**（模型自主 PAUSE 后，回来是否需要显式条件、条件从哪来）。它同时是修好"以后不再产生无资格 PAUSED"的正解，但**不回溯解决本遗留实例**，除非允许对存量活动补规则——那又是另一个决定。

## 方案与推荐

### 方案 A：只补已有明确规则对应的窄不一致

- **A1（推荐，若要做）**：把 LR-4 显式 RESUME 的状态前置从 `{WAITING}` 放宽到 `{WAITING, PAUSED}`，其余门全部不动。理由：设计 SRC-0719 允许 PAUSED 按自身恢复条件回来；LR-2 已支持；AG-1 已展示该选项；不新增候选、不放宽 eligibility 开闭、不改 revision/幂等/前台冲突。
- **A2（严格零行为变更的替代）**：反向收窄 AG-1，PAUSED 不再给 RESUME。它让"展示集 = 可执行集"，但**与设计 SRC-0719 的 PAUSED 可恢复相悖**，只是把不一致藏起来。不推荐，除非监工判定 A1 属于产品可见行为。
- **A 的共同边界**：都不产生可运行路径，不解本实例，不构成 G1 证据。

### 方案 B：保留行为规则，把真正新增选择的部分停问

需要本人回答的产品问题只有三个，按优先级：

1. 模型自主 PAUSE 的活动，之后能否由新 Decision 直接 RESUME（B2b）？若可以，PAUSE 时是否必须绑定恢复条件（B3）？
2. 无 eligibility 的 PAUSED 活动，是否允许 chooser 直接 ABANDON（B2a）？若允许，终止后的承诺/未决副作用按 SRC-0719 Q10 怎么记？
3. 存量的无资格 PAUSED（含本实例）是否适用新规则、还是只对新建活动生效？

### 推荐

**A1 + B 组合：先做 A1 这一条窄对齐（1 代码 + 1 测试 + 1 note），同时把 B2a/B2b/B3 作为三个产品问题停下等本人拍板。**

- 可立即做：A1。它不改资格、不改权限、不改版本/幂等/前台冲突门，属于"沿用既有机制的最小修复"。
- 不可立即做：B2a、B2b、B3，以及任何对存量活动的补写。**尤其是不要为了解卡手工补 eligibility、目标、completion，也不要用旧 START 重放。**
- 明确结论：**规范不支持"自动解卡"。** 即使 A1 修好，本实例仍因缺少 eligibility、World 目标、完成契约而无法完成 G1。本次无法在无行为决定的前提下完成 G1。
- 不建议再运行真实模型碰运气，也不建议手动终结该活动。

## 精确写集与验收

仅在监工批准 A1 时适用。写集 3 个文件，同一时刻只允许一个 writer：

1. `components/life/scripts/activity_waiting_agenda.py`（只改 `explicit_resume_from_eligibility` 的状态谓词，保留 OPEN/过期/revision/前台/CAS/幂等全部检查）。
2. `tests/test_day_life_decision_legality.py` 或新建同目录窄测试（只在既有测试文件里加例时算 1 个写集文件；新文件则写集变 4，需监工确认）。
3. `.agents/notes/implemented/bug-fix/2026-10-23-paused-resume-eligibility-alignment.md`（按 write-notes 规范，含 Problem/Decision/Alternatives/Consequences）。

具名正负控：

- **正控**：前台 PAUSED 活动 + OPEN、未过期、revision 相等的 eligibility → AG-1 给 RESUME，LR-4 接受，LR-2 提交 PAUSED→ACTIVE，eligibility 变 `CONSUMED`。
- **负控 1**：eligibility 非 OPEN（CONSUMED/CLOSED）→ 仍抛 `EligibilityNotOpenError`。
- **负控 2**：eligibility 已过期 → 仍抛 `EligibilityNotOpenError`。
- **负控 3**：`activity_revision` 不一致 → 仍抛 `StaleResumeConditionError`。
- **负控 4**：WAITING 活动的既有 RESUME 路径逐字节不变（回归）。
- **负控 5**：前台是别的活动时，RESUME 仍被前台冲突拒（不得因放宽状态而顺带放宽前台门）。

验收命令（≤5 条，全部离线，不碰实例）：

```bash
python -X utf8 -m unittest tests.test_day_life_decision_legality -v
python -X utf8 scripts/notes_tree.py
python -X utf8 scripts/notes_format.py
git diff --check
python -X utf8 -c "import json,pathlib;print('READONLY_CHECKS_ONLY')"
```

最后一条只是占位式的"不碰实例"声明，若监工要求现场只读核对，改为既有 `day_capability_probe.py status`，且不得因此新增产品调用。

**本单实际执行的只读命令与退出码**（本次未运行任何测试/实例命令）：

- `ls`/`grep` 设计目录与 `原文分章/SRC-0719.md`、`SRC-0007.md`、`SRC-0233.md`、`12-工程基线协调与真实宿主映射.md`：退出码 0。
- `sed`/`grep` 读 5 份实现/测试文件命中段（`agency_decision_ag1.py`、`candidate_sources_ag0.py`、`activity_continuity.py`、`activity_waiting_agenda.py`、`tests/test_day_life_decision_legality.py`）：退出码 0。
- `grep` 读 3 篇 note 命中段（`2026-10-26-v04-day-life-decision-legality.md`、`2026-10-28-v04-wait-closeout-core.md`、`2026-11-15-v04-life-pacing-breakpoint-and-resume.md`）：退出码 0。

## 未验证

- **未运行任何测试**；本单是规范对齐核查，不是行为验证。上面 A1 的正负控是建议，不是已执行结果。
- **B1 的可达性未证**：没有证明"PAUSED + OPEN + revision 匹配"能在现有词汇下自然出现；只读了 5 份文件的相关片段，没沿 eligibility 生成/消费的全部分叉。
- **AG-2 未独立读**：RESUME 采纳到 LR-4 的调用链引用 `v04-g1-resume-path-20261023-result.md` 与 `life_integration_ag2.py` 的既往结论，本次未复读，按"引用未独立复现"处理。
- **World 目标与 completion 缺口未独立复现**：只引用前述报告。
- **实例状态未复测**：未连 WSL、未读账本、未核对 lease/进程；`revision15`、`ledger13`、`共同14/96余82` 均为历史值，不是实时保证。
- **未验证真实模型面对 PAUSED 无候选时会选什么**：本单 0 产品调用，也没有为此起服务。
- **规范侧只覆盖命中段**：未通读总纲，未读 v3.0（按工单封存）；如果设计其他章节对 PAUSED 恢复有更具体规定，本单可能漏检，故标 UNKNOWN 而非"规范无要求"。
