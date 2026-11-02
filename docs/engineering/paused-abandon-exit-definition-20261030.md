# 后台 PAUSED 的显式 ABANDON：现有门与规则定义

日期：2026-10-30。状态：worker 只读交付经父级有限审阅，不是施工或验证批准。标记【已核】指 worker 的具名现树定位，不代表父级逐行复审或本轮运行证明；【拟议】未实现；【UNKNOWN】本次未核清。

结论先说：LR-2 有 PAUSED 直接 ABANDON 的状态转换能力，不必先转 WAITING；但所核正式 AG-1 validator 与 AG-2 都明确拒绝 PAUSED，没有可提交安全证明后放行的现成分支。因此不是「底层没有路」，也不是「只差把既有完整证明口接上」。本次扫描没有确认任何同时满足完整副作用与承诺证明的可放行子集；这不等于证明全系统不存在。其余部分未获安全证明，不能靠本窗口或监护批准放宽，政策变更只能交本人裁定。

## 一、现树各层实际要求【已核】

- **AG-0 `CurrentActivityCandidateSource`**（`candidate_sources_ag0.py:1092-1281`）：只读 `foreground_activity_ref`；ACTIVE 或 **legacy PAUSED**（指针仍指向自己）发射 `CONTINUE_CURRENT`；WAITING / INTERRUPTED / terminal / `external_stop.state == PENDING` 不发射。新规则下 PAUSE 已释放前台，故新挂起 PAUSED 根本不进此源；它的入口只有 `RESUME_ELIGIBLE`（需 OPEN 资格）。
- **AG-1 合法集**（`agency_decision_ag1.py:3029-3124`）：CONTINUE 分支仅在 `cur_status == ACTIVE` 给 `PAUSE/ABANDON`（3053-3055）；PAUSED 分支被明确写成“WITHHELD, not offered”（3076-3084），`CONTINUE/WAIT/PAUSE/RESUME` 也都不给。RESUME_ELIGIBLE 分支的 ABANDON 额外要求 `cur_status != STATUS_PAUSED`（3118-3124）。唯一非前台的 ABANDON 通道是 BLOCKED 源（3064-3074）。
- **AG-1 硬约束**（`:2560-2590`）：`act_status != ACTIVE` 一律 `act_not_active`，唯一例外 `paused_foreground_self`（2583）；但该例外只让候选存活，不产生 ABANDON。
- **AG-1 validator**（`:3412-3447`）：`cur_status == PAUSED` 的 ABANDON 在 3417 直接抛 `PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE`；3435-3439 的“只能放弃前台自身”防伪造不可达。
- **AG-2**（`life_integration_ag2.py:3992-4050`）：签发预提交授权前，目标记录 status == PAUSED 即抛 `PAUSED_ABANDON_WITHHELD_UNPROVEN_SAFE`（4006-4010）。**无条件扣留，接口不接受任何安全证明参数。**
- **LR-2**（`activity_continuity.py:3008-3052`）：`abandon_activity` 允许 ACTIVE/PAUSED/WAITING/BLOCKED（3033）。前台检查：目标不是前台时，终止转换只放行 `from_status ∈ {WAITING, PAUSED, BLOCKED}` 且 `foreground_released is True`（2415-2421）。另有 revision CAS（2363-2367）、幂等 replay（2360）、terminal immutable（2374-2378）。**这一层本来就允许，无需改。**
- **后台目标的身份**：`DecisionContextSnapshot.current_activity_ref` 只取 `foreground_activity_ref`（`agency_decision_ag1.py:1787-1791`），快照不携带后台挂起，也不携带 `foreground_released`（3109-3113 注释自认）。因此现树**没有后台 PAUSED 目标**：无候选、无合法集、无目标字段。当前不存在“前台 fallback 串到另一个 Activity”的运行时路径（因为根本不给 ABANDON），但任何新出口必须由候选自身 ref 绑定目标，禁止回落 foreground。

## 二、安全门要的事实与现树可读性【已核，除注明】

- **未决副作用**：AR-0 状态含 `PROPOSED/PREPARED/SCHEDULED/SUBMITTED/ACKNOWLEDGED/UNKNOWN/SUCCEEDED/FAILED/CANCELLED`（`action_reality_ledger.py:132-152`），终态为 `SUCCEEDED/FAILED/CANCELLED`（230-232），`UNKNOWN` 是显式状态且可再转（223-227）。
- 现成证明口 `_blocked_exit_safety(activity_id)`（`life_runtime_production.py:713-746`）：读 **AG-2 集成记录** `integration_ids_by_activity[activity_id]` → `action_refs` → `get_action(ref).status`；status 不在 `{SUCCEEDED, FAILED}` 即 unresolved；读失败/缺失返回 `readable: False`。**它不依赖 `actv:/actv_` 匹配**，旧 note 的 id 词表冲突在这个口上已不成立；但 `CANCELLED` 也被当成 unresolved（更保守）。
- 该口目前**只接在 BLOCKED 源**（`life_runtime_production.py:751-752`），PAUSED 路径没有接。
- **覆盖面 UNKNOWN**：只有经 AG-2 集成并写入 `integration_ids_by_activity` 的 Action 才可见。若某 Activity 的 Action 由其他路径提交、没有集成记录，该口给出 `readable: True, unresolved: False`——“空”不证明“没有”。本次未核所有 Action 提交路径。
- **Action 身份**：`ActionProposal.activity_ref` 必须 `actv_` 前缀（`action_reality_ledger.py:625`），canonical 是 `actv:`；不是字符串重写，是两套前缀且无映射。精确绑定只能经集成记录的 `action_refs`，不能反查 Action 表。
- **承诺证明缺口**：所核生产 `CommitmentCandidateSource` 默认 DISABLED，`MISSING_CANONICAL_COMMITMENT_AUTHORITY`（`candidate_sources_ag0.py:2140-2174`）。这能证明该来源未提供权威，不能推出全树没有任何承诺权威。本次未找到能被 PAUSED 放弃采纳链消费的完整无承诺证明；空 `commitment_constraint_refs`、absent、UNKNOWN 或未登记均不可证明「没有承诺」。
- 历史 `partial_stage == ACTION_UNKNOWN_UNSETTLED` 与 `ACTIVITY_COMMITTED_ACTION_MISSING`（`life_integration_ag2.py:5627/6228/6244`）属于未结算，应计入 unresolved；历史 `FAILED` 是终结，不永久禁止。

## 三、允许与拒绝条件【拟议】

允许（全部满足）：
1. 新鲜 Decision 显式选中该 PAUSED Activity 的候选，候选 `activity_ref` 即目标，AG-1 校验相等，不回落 foreground；
2. 未决副作用证明返回 `readable: True` 且 `unresolved: False`，且该证明的覆盖面可声明完整（否则不算通过）；
3. 权威、完整地证明无未处理承诺绑定；「不来自承诺源」单独不足以证明后来没有绑定，来源排除只有具备已批准的完整约束合同才可作为证据，本次未证；
4. `interruptibility != ATOMIC`；revision/CAS 新鲜；AG-2 预提交授权成功；不抢占任何前台。

任一 `UNKNOWN`、读失败、缺字段、权威不存在、覆盖不全 → 拒绝，且拒绝具名，不假装成“没有”。

## 四、完整路径与接缝分类

- 候选：**新接线**（现树无后台 PAUSED 的可放弃候选；BLOCKED 源形状可参照，不直接复用状态）。
- 拟议最小合法集：仅对本重考候选给 `DEFER`，并在完整安全证明通过时追加 `ABANDON`；原全局 `NO_ACTION` 保留。不得借此给 `CONTINUE/START/WAIT/PAUSE/RESUME` 或改条件；缺证明不展示 ABANDON，不能提交一个必被 AG-2 拒的假选项。候选的具体既有 kind/source 承载方式尚未确定，不承诺无需协议变化。
- AG-1 合法集 + validator：**新受控放行分支，待批准**。若只补足旧安全要求并以既有权威证明，可能属于恢复已有规则接线；本次尚未证明前提成立，不能直接按窄修施工。若降低证明标准则明确是安全政策变更，须本人。
- AG-2：**新受控放行分支，待批准**（无条件扣留 → 对完整权威证明再核验）；不能为了开出口把 UNKNOWN 或空列表当安全。
- LR-2 / 状态机 / 幂等 / 终态：**既有，无需改**。
- 成功判据：AG-2 采纳 `APPLIED` + LR-2 收到 ABANDON 回执 + 目标 status 变 ABANDONED；否则如实报拒绝原因，不报成功。

## 五、终态之后

- 记录保留全部停点、来源、条件、历史；terminal immutable 保证不复活（`activity_continuity.py:2374`）。
- 仅同一操作身份的可核幂等回执可复用；已经 ABANDONED 的回读不冒本 Decision 新完成一次放弃，其他 terminal 不得冒 ABANDON 成功；不二次终结、不裸重试。具体重复结果形状本次未运行验证。
- 别的前台保持不动：LR-2 只对目标做转换，非前台 PAUSED 走 `foreground_released` 分支（2415-2421）。
- LR-4 条件/资格在 AG-2 调用 `reconcile_with_canonical_activities(now_at=occ_iso)`（4050）后按既有 reconcile 失效。**精确 status（是否 CANCELLED/STALE）本次未核 → UNKNOWN**；不得写成 SATISFIED/CONSUMED，也不得假定自动清理。
- 跨 owner 非原子：授权→LR-2→adoption/条件维护存在间隙。原操作的幂等能力不等于所有失败都能自动补齐；失败须按原身份查实际状态，不重放未知效果。LR-4 尚未 reconcile 时也必须由 canonical terminal 宿主检查扣留旧资格；这项正式路径本次未核清，不宣称已经保证。安全证明读取后到终态前的并发新 Action/承诺写入防线同样 UNKNOWN，不能把一次读空当原子无副作用证明。
- 未结 Action 不标成功、不删除、不裸重试；承诺后果不凭空消失。

## 六、与既有边界的关系

- TTL / no_change 只判老化，不产生本出口；同版本合法 CONTINUE 的既有路径不受影响。
- 不改 head/body/park 顺序，不自动、不批量、不抢占，不破单前台。
- **三项缺口必须先有答案，不批准先施工**：①按 Activity 的完整未决副作用证明及并发有效性；②完整无未处理承诺绑定的权威证明；③候选/快照精确绑定后台目标及释放事实。若能用既有权威补齐，可以再审是否只是接线；若需要新增权威/覆盖声明合同，先交本人定；若只能降低安全要求，出口保持不可用。监护审阅本 doc 不代表本人批准松门。

## 七、两类结果与未核项（父级收窄）

- **能权威满足的子集**：本扫描未确认任何 PAUSED Activity 同时满足全部证明，不能给出数量或宣称正控已成立。既有 BLOCKED 读口可读取部分集成 Action 的具体状态，这是有用能力，但不足以推出 PAUSED 安全集合非空。
- **不能证明的部分**：Action 覆盖未证完整；空引用无法证明无承诺；后台目标和安全事实没有现成 PAUSED 放行链。缺少任何一项均保持出口不可用，不按概率或常识放行。
- **覆盖六接缝**：AG-0、AG-1 合法集/validator、AG-2、LR-2、Action 读口和承诺来源均有具名定位；LR-4 终态后精确 condition/eligibility status、并发安全证明有效性、全部 Action 提交路径未核。本 worker 没有读 `activity_waiting_agenda.py`，因此不冒完整六层生命周期图或条件收口已查全；本轮不追加第二试。

## 八、仅在门可证通过时的实施与离线建议（未批准、未运行）

这不是开工写集。只有先确认上述完整权威证明成立，才讨论：`life_runtime_production.py` 的既有安全读口接线；`candidate_sources_ag0.py` 的后台重考投影；`agency_decision_ag1.py` 的精确后台目标与受限合法集；`life_integration_ag2.py` 的采纳前新鲜复核及原 LR-2 命令；必要的 `autonomous_life_cycle.py` 结果回读和 `life_step_readiness.py` 原记录到期投影；单个新 `tests/test_paused_abandon_reconsideration.py`。这些文件是职责候选，未核实每个都需要改；不增加持久格式，不改 LR-2/LR-4/owner，不碰 body/park 或默认门。

最小离线建议是一个模块、一条正式脚本 Decision 主链，既有真实 owner、禁网 transport，检查安全可证后台目标经 AG-1 提交→AG-2 采纳→LR-2 ABANDONED，另一前台完全不动，条件终态收口不得产生 OPEN；附 Action UNKNOWN/在途、承诺未读/空/有绑定、读失败、错目标/CAS 的直接拒绝，以及同原操作幂等与终态不复活。若缺真实证明口，不能手塞 `safe=true` 冒主链成立；应停在严格拒绝。无安全子集就不运行上述正控，也不编造证明接口。这只是按最新监护要求预想形状，尚未获验证范围批准，不列可立即执行的命令。

## 九、交本人裁定的选项（不预选）

1. 保持现门，承认本出口目前不可用，不新开产品试跑；保存已核路径与缺口。
2. 本人另批「完整安全证明合同」的范围：权威来源、Activity/Action 绑定与覆盖、无承诺证明、并发有效性及旧数据 UNKNOWN。先解决证明，不先松拒绝；新增持久格式/owner/跨层协议若必要须明确批准，不混进 v1。

降低证明标准属于另一个安全政策决定，本文件不提出默认放宽，不把监护排期或凌晨一点授权当作该决定。
