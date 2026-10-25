# Agent Note: 有效搁置事项在安静门之前成为候选

Status: implemented

## Problem

到期检查窗（`CHECK_WINDOW`）里，一个实例可能只剩一件真实可考虑的事：**她放下的活动，现在有了
LR-4 的 OPEN eligibility，可以回来**。这个状态当时到不了 Decision：

- `AutonomousLifeCycle.materials()` 的 `has_material` 只看前台活动、续作作品、阅读材料、World
  机会、到期事项与项目选项。WAITING 且 `release_foreground=True` 的活动不占前台，若又没有别的
  材料，`has_material` 为假，`evaluate_once` 在调用 AG-0 之前就返回 `NO_MATERIAL`
  （`autonomous_life_cycle.py:5149` 一带）。资格再有效，也没有任何一层看到它。
- 既有 QUIET 指纹里已经有 `resume_eligibility_refs`，所以「新开一条资格会重新打开问题」本来
  成立；真正断掉的是更前面那一步：**一旦没有别的材料，连指纹都不用算**。
- AG-0 的 `ResumeEligibleCandidateSource` 自身还接受三类 LR-4/LR-2 不会放行的行：已过
  `expires_at` 的资格、`waiting_episode_id` 与活动不一致的资格、以及另一个 PAUSED/ACTIVE
  活动占着唯一前台时的 RESUME。它们会被 materialize，然后在 LR-1/LR-2 或 AG-1 才被拒。

## Decision

只补供给与有效性，不新增第二套资格或供给器。

1. `AutonomousLifeCycle.materials()` 在原有材料判断之后，向 **同一个** AG-0
   `RESUME_ELIGIBLE` 源（`parked_resume_candidates`）做一次只读查询，并把
   `parked_candidates.count > 0` 计入 `has_material`。这样有效搁置事项在原 QUIET 判断之前
   就真实可见。查询失败、源未接线或被禁用一律按 **0 条材料** 处理，并具名报告
   （`RESUME_SOURCE_READ_FAILED` / `RESUME_SOURCE_NOT_WIRED` / `RESUME_SOURCE_DISABLED`）。
2. `ResumeEligibleCandidateSource.validate_source` 与 `refresh` 增加三项既有权威规则的镜像：
   - `expires_at` 已过 → 拒绝（与 `WaitingCoordinator.explicit_resume_from_eligibility` 同一
     比较方向）；
   - 资格与活动的 `waiting_episode_id` 都存在且不等 → 拒绝（与
     `_collect_stale_and_terminal_updates` 的资格分支一致）；
   - 另一个前台既不是 `WAITING`（LR-2 的后台等待情形）也不指向本活动 → 拒绝
     `FOREGROUND_CONFLICT`（与 AG-1 `compute_legal_decisions_for_candidate` 的
     `foreground_holds_occupancy` 判据一致）。
   读不出的时间、活动视图或前台活动按失败关闭处理。
3. `refresh` 在预采纳复查时重跑同样的三条，所以「展示后被撤权 / 过期 / 换成别的状态」仍然在
   正式采纳前被拒。
4. 目标**来源准入**（展示前就回答，不推迟到执行阶段）：Activity 的 `subject_refs` 是 AG-2
   把 adoption spec 的 `target_refs` 写进去的正式绑定（`life_integration_ag2.py:3339`）；
   `origin_ref` 同样是活动自己的来源字段。对绑定了一个或多个 `artifact:` ref 的活动，
   `ResumeEligibleCandidateSource` 通过既有的 `LifeArtifactPort.read_artifact` 逐个证明
   owner 仍按 subject 提供它（不剥前缀、不猜 id、不新增 owner 合同）；读失败、owner 拒绝、
   记录缺失或读取口未接一律 `TARGET_SOURCE_UNREADABLE` / `TARGET_SOURCE_MISSING` /
   `TARGET_SOURCE_READ_NOT_WIRED`，不展示。`origin_ref` 是 Artifact 却没有 Artifact 绑定的
   活动是 `TARGET_BINDING_MISSING`。非 Artifact 活动（World/future-item 等来源）没有 Artifact
   声明，保留它原有的 Activity owner 校验，**不**被本规则无条件放行，也**不**被误删。
   `refresh` 在展示后重问同一个读口，所以目标被删除/撤除后，旧候选在正式采纳前被拒。
   边界要写清：Supply 的读路径是 subject 归属，不是 grant 作用域；撤销
   `COMMIT_MANAGED_ARTIFACT` 写授权不会让 subject 失去读权，因此本单不以 grant 状态冒充
   「来源撤权」。该 owner 合同里可证的来源撤销形态是 tombstone/删除/不可读；若产品要求
   「写 grant 一撤销就不可展示」，现有 owner 没有这个状态，需要另行决定，本单不自行扩合同。

明确不改：`ReconsiderationLedger` 的 fingerprint 输入、TTL、`block_situation` 谓词、
`open_resume_refs` 的 OPEN-id 读法、调度/窗口/预算/状态枚举/落盘 schema、AG1/AG2/LR2/LR4 的
权威规则；不消费资格、不恢复活动、不提交 Decision/Action、不制造 OPEN、不新增 deadline。

## Alternatives considered

- **新建一个「搁置事项供给器」或第二个资格系统**：最强理由是职责单一、测试容易隔离。
  否决：资格的唯一权威是 LR-4，候选的唯一正式读口是 AG-0；再造一份会让「有效」在两处漂移，
  而且会多一个必须保持同步的候选身份。
- **直接把 `open_resume_refs()` 的 OPEN id 当材料**：改动最小，只看一个文件。
  否决：这正是把「名义 OPEN」当「现在可回来」。过期、episode 不匹配、前台冲突的行都会变成
  材料，窗口被无意义地打开；任务要求失败关闭。
- **在 `block_situation` 里加一条「有资格就不 QUIET」**：表面最直接。
  否决：那是改安静谓词本身，会撕掉「同一状态已考虑过就不重复」的既有去重；资格 id 本来就在
  指纹里，正确的缺口在材料判断，不在去重门。
- **无资格时也把搁置活动摆出来，或自动 RESUME**：能解遗留实例。
  否决：无资格恢复是产品/契约决定（见
  [PAUSED 资格对齐](../bug-fix/2026-10-23-paused-resume-eligibility-alignment.md)），自动恢复
  等于替她做意愿；本单只让「已经有效」的选项可见。
- **把前台冲突全部过滤掉，包括 ACTIVE 和 WAITING**：看起来最保守。
  否决：WAITING 前台是 LR-2 允许的后台等待情形，ACTIVE 冲突由 AG-2 既有预暂停处理；全过滤
  会删掉合法候选。这里只镜像 AG-1 已经拒绝的那一档（非 WAITING 的他人前台）。
- **把目标来源准入推迟到执行/采纳阶段**：改动最小，只靠既有复查。
  否决：那正是本单要补的缺口——一个目标已经被删除或 owner 拒绝读的活动会在安静门之前被
  当成材料并展示；「执行时会拒」不能替代「展示前证明来源还在」。
- **对所有非 Artifact 活动也加一条失败关闭**：看似最严格。
  否决：本 adapter 没有 World/future-item 的来源读口，硬关会删掉既有合法候选，把「无法证明」
  冒充成「来源已撤」。对非 Artifact 只保留既有 Activity owner 校验，并如实标出这一边界。
- **用 grant 状态证明「来源未撤权」**：看起来更完整。
  否决：ManagedArtifact 的读路径按 subject 归属，不按 grant；写 grant 的 revoke 不改变读
  可见性，拿它当读来源证据会把两种权限混成一种，且需要新合同。本单只用 owner 的读合同。

## Consequences

- 一个只有「放下的事 + 有效资格」的实例，现在会在到期窗口里真正考虑一次；选择 `NO_ACTION`
  仍是合法的，下一次同状态仍是 `QUIET`（资格 id 仍在指纹里），不会自动回来。
- `NO_MATERIAL` 的语义更窄：只剩无效资格/不可读资格时仍然是 `NO_MATERIAL`，且不花请求。
- 已知边界：
  - 「来源」对 Artifact 目标 = 该 subject 仍能从 Supply owner 读出这件 Artifact
    （不存在/tombstone/owner 不可达都会失败关闭）；它不是 grant 作用域的重新建模，也不覆盖
    「写 grant 被撤销但读仍可用」这一产品语义缺口。非 Artifact 活动的来源权威不在本 adapter
    的读口内，只沿用 Activity owner 的既有校验。
  - 测试与冒烟的 OPEN eligibility 来源不同：单测里是合成记录（仓库没有给 WAITING 活动
    mint 资格的公开发口）；冒烟里由真实 `scan_due_agenda` 从 operator 选择的时间条件发出，
    资格本身是真实 owner 产出的，但时间条件是夹具，仍不是「她自然等到条件」的证据。
  - 真实 Supply owner 只在 POSIX 隔离 socket 上验证；Decision/admission 读端口仍是替身，
    没有真实模型采用，也没有真实实例被启动。不证明 G1、不证明 24h；本单 0 产品调用。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_parked_candidate_supply`
  → 28 例通过。原有 20 例（合成资格、替身 Decision）之外，新增 8 例使用**真实**
  `LifeSupplyService` + 生产 `LifeArtifactPort` 的隔离 socket：真实目标正控、未接读口、
  tombstone 删除（资格仍 OPEN）、展示后删除在 `refresh` 拒、owner 不可达、artifact origin
  无绑定、非 Artifact 活动不被误删、写 grant 撤销不改变读合同。
- 相关回归 `tests.test_life_wait_resume_v04` + `tests.test_paused_eligible_alignment` +
  `tests.test_day_life_quiet_runner` → 39 例通过。此前
  `test_a_round_without_a_dependency_sends_no_basis_at_all` 的红：先把本单两个业务模块的
  可识别增量在一个保留其余 dirty 的临时副本里撤掉，同一用例仍红且 `parkbasis` ref 完全相同
  （`parkbasis:a6a49d34fe9657a2`），证明它来自既有的「自选时间搁下」park offer，不是本单。
  该 fixture 改为断言「没有 dependency basis / 静态 dependency spec」，同时明确 park basis
  合法；原「不得凭空发明依赖」的断言保留。
- `python3 -B scripts/parked_candidate_supply_smoke.py` → exit 0，真实 LR-2/LR-4/LifeSupply
  owner、0 模型、仅两个隔离 owner socket。资格由真实 `scan_due_agenda` 从时间条件发出；首窗
  `NO_ACTION` 且 AG-1 合法集含 `RESUME`，活动仍 WAITING、资格仍 OPEN；下一窗 `QUIET`
  （0 请求）。负控：过期、另一前台、tombstone 后 0 条、展示后 tombstone 在预采纳 `refresh`
  拒、owner 停止后 0 条，且 target 丢失后资格仍 OPEN（与资格负控分开）。
- 相关决定：[wait-closeout](2026-10-28-v04-wait-closeout-core.md)（资格由 LR-4 的唯一 due scan
  产生，恢复仍是新 Decision）、[paused 资格对齐](../bug-fix/2026-10-23-paused-resume-eligibility-alignment.md)
  （PAUSED 的 OPEN 资格规则）、[wake 候选成本](../bug-fix/2026-10-24-wake-candidate-cost.md)
  （同一回合复用 owner 读；本单新增的是同一源的一次本地 store 读，不跨回合缓存）。
- 后续（2026-10-24）：PAUSED 也能作为条件宿主，通过
  [显式条件 PAUSE](2026-10-24-paused-explicit-resume-condition.md) 生成 OPEN 资格；本笔记的
  候选供给与来源准入规则不变。
- 后续（2026-10-24，PAUSE 释放执行槽）：新产生的挂起 Activity 不再占前台，因此它自身的
  RESUME 候选不会因为“PAUSED 前台冲突”被挡；真正的另一 ACTIVE 前台仍然冲突。旧 PAUSED
  指针没有释放标记，仍按本笔记的冲突规则处理。见
  [PAUSE 释放执行槽](2026-10-24-paused-background-single-execution.md)。
