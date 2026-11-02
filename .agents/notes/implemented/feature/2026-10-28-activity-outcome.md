# Agent Note: Activity FAILED / BLOCKED——做砸与卡住都要有正式收场

Status: implemented

## Problem

Design v2 Q8/Q10 早就有 FAILED/BLOCKED/CANCELLED 与「Action FAILED 只是证据、UNKNOWN 不得改写成失败」的规则，但代码只有 COMPLETED/ABANDONED/CANCELLED/EXPIRED。没有终态失败，一次做砸只能被写成 ABANDONED 或永久 ACTIVE；没有 BLOCKED，卡住只能借 PAUSED/WAITING 冒充，语义和恢复路径都不对。首交后仍有两个口子：无条件 BLOCKED 没有模型可选的退出，未完成材料里看不到 BLOCKED；FAILED 的 AG-2 交接只在 demo 里手写 emit，不是正式入口。

## Decision

按方案 A 扩 LR-2 既有状态机：FAILED 终态只由 S09 owner 用精确 activity/revision/目标范围 + 封闭判据 + 可重读 owner 证据经 ACTIVITY_CONSEQUENCE 提交，判据必须被证据真正支持（UNKNOWN/timeout 的 Action 或结算不是「不可达」），保留 checkpoint/真实版本/未决 Action refs、不写 completion；BLOCKED 非终态，owner 记具名依据并释放前台，恢复只走 LR-4 condition→OPEN→新 Decision RESUME。新记录用 activity.v2/transition.v2，v1 保持可读不迁移。正式 fail 入口（runtime/CLI）在 owner 转换成功后立即走 AG-2 的 `emit_experience_handoff(FAILED)`，同 operation 重放复用原事件；AG-2 reconcile Phase F 仍是持久恢复入口。BLOCKED 新增一个窄候选 source（只投影 `BLOCKED_ACTIVITY`），AG-1 只给它 ABANDON，source 对 ATOMIC/未决 Action/读不到安全事实的件具名扣留；BLOCKED 同时进入 materials 与 continuity 的 unfinished，明确不可 CONTINUE/START 绕同件，无 OPEN 资格不可 RESUME。

## Alternatives considered

方案 B（独立 ActivityOutcome manager + 影子状态库）隔离迭代更好，但会产生第二 Activity 权威、双重恢复和两套结算，违背 LR-2 单一 owner；否决后只在 admission 增加窄 evidence verifier，不新建 store。另一个备选是让 demo 继续手写 emit、由 dispatcher 事后补投 FAILED，但那证明不了正式入口会交接；否决原因是任务要求入口本身完成一次且可重放。

## Consequences

做砸后 Activity 结束、前台释放、真实成果与失败判据/证据都留着，新工作是新的 Activity；卡住后可以靠明确条件回来，也可以由新 Decision 合法 ABANDON，但没有任何东西替她自动回来/自动放弃。代价是新增两个状态值和一个候选 kind/source，旧 reader 会按 schema 版本拒绝 v2（不是静默误读），BLOCK 的「依据」是结构化具名事实而非对条件真值的独立复核，SOURCE_WITHDRAWN 判据目前没有 owner 读口所以只能被拒。验证：`scripts/activity_outcome_demo.py` 真 Supply/runtime/真 M0 consumer 跑通 A 失败→E 无条件阻塞→ABANDON→C 真实 UNKNOWN→拒失败→FAILED→迟到成功不复活→B 条件 OPEN→RESUME，共 21+ 项检查；`tests/test_activity_outcome.py` 24 例。

## 2026-10-28 收尾：判据必须属于本 Activity

- Problem：原判据只核 owner+状态，证据与 Activity 的目标没有绑定。修前实测两处被接受：A 未归档而 B（别件作品）已归档，用 `artifact:B` 给 A 签 FAILED；用别件的 FAILED receipt 走 `EXTERNAL_TERMINAL_REFUSAL` 也能签。等于别件的失败能给本件收场。
- Decision：证据对与解析出的 owner 行成对校验。Artifact 判据要求 owner 行 `artifact_id` 按精确 id 属于本 Activity 已绑定 scope（`_activity_goal_scope` 的 artifact* refs，剥掉 owner 自带 `artifact:` 前缀后比较，禁前缀/标题/hash）。`EXTERNAL_TERMINAL_REFUSAL` 要求行 `activity_ref` 指向本 Activity **且** owner 行带显式 `terminal_refusal` 终局事实；现有 owner 记录没有该事实，故本版具名不支持、不把一次 FAILED/UNKNOWN 当终局。未决 Action 的 `activity_ref` 必须指向本 Activity。admission 复用同一 `validate_failure_payload_against_activity`，提交前按 live canonical Activity 重核，两处同修。
- Alternatives considered：只信调用者传入的 `goal_scope_refs` 最简单，但 scope 只是「子集合法」，与证据无关联，别件归档照样能签本件——否决。把任何 `FAILED` Action/结算都当终局拒绝实现成本最低，但 GATE_OFF/版本 CAS/预算拒绝都会把「一次没做成」写成「目标永不可能」，与 v2「Action FAILED 只是证据」冲突——否决，改为具名不支持。
- Consequences：别件证据、别件未决 Action、UNKNOWN/timeout 都在提交前被拒，A 的 status/revision/checkpoint/last_action/handoff 零变；本件真实归档正控、BLOCKED 退出、迟到成功不复活原样通过。代价是 `EXTERNAL_TERMINAL_REFUSAL` 暂不可用（待 owner 增加终局凭据再开），且每种判据多一次按 Activity 的 scope 归属核对。验证：demo 新增别件归档负控（assess+fail 双入口）与真实 UNKNOWN receipt 语义负控，40 项检查全绿；`tests/test_activity_outcome.py` 30 例（含 admission verifier 直接负控；修前 5 例反例被接受，修后全拒）。
