# Agent Note: PAUSED 活动的恢复资格在两处状态谓词上对齐

Status: implemented

## Problem

AG-1 的合法决策集已经给「PAUSED 且带 OPEN eligibility」的活动提供 `RESUME`
（`agency_decision_ag1.DecisionOutputValidator.compute_legal_decisions_for_candidate`，
`CANDIDATE_KIND_RESUME_ACTIVITY` 分支不看 Activity 状态），LR-2
`ActivityCommandService.resume_activity` 也允许 `PAUSED` 和 `WAITING` 两种来源状态。
但 LR-4 的显式恢复入口 `WaitingCoordinator.explicit_resume_from_eligibility` 和它调用的
`reconcile_with_canonical_activities` 里的资格清理，各有一处同源状态谓词只认 `WAITING`：

- 入口先 reconcile、再读资格。`_collect_stale_and_terminal_updates` 的 eligibility 分支
  把任何非 `WAITING` 活动上的 OPEN eligibility 直接改写成 `STALE`，入口随后看到
  `status != OPEN` 就拒绝。上一单只放宽入口谓词并不可行，受阻报告已记在
  [v04-paused-eligible-alignment-20261023-worker-result.md](../../../docs/engineering/v04-paused-eligible-alignment-20261023-worker-result.md)；
  当时用仓库外临时副本验证「两处都改才通」，副本不是交付。
- 结果是同一层业主给出的合法选项，被下一层必然拒绝：AG-1 让模型选 `RESUME`，LR-4 必抛
  `EligibilityNotOpenError`。设计基线允许这种回来路：设计 2.0 分章 SRC-0719 第 10–13 行写
  PAUSED 在各自恢复条件满足后可回 READY，SRC-0007 第 16 行把暂停与等待都列为合法
  transition intent。既有的 wait-closeout 笔记也把「PAUSED 的回来之路是 LR-4 eligibility +
  RESUME」当成前提，见 [2026-10-28-v04-wait-closeout-core.md](../feature/2026-10-28-v04-wait-closeout-core.md)。

## Decision

只对齐这两处**状态谓词**，从「必须 `WAITING`」改为「`WAITING` 或 `PAUSED`」，
`components/life/scripts/activity_waiting_agenda.py` 内两行：

- `explicit_resume_from_eligibility`：目标 Activity 状态检查。
- `_collect_stale_and_terminal_updates` 的 **eligibility 分支**：状态判定。

其余判据一条不动：`OPEN` 状态本身、过期时间、`activity_revision` 匹配、
`waiting_episode_id` 谓词、`CAS`/`expected_revision`、幂等、身份/中断门、前台占用
（仍由 LR-2 的 `ForegroundConflictError` 拒绝）、terminal 活动先被取消。
condition 分支与 agenda 分支保持「必须 `WAITING`」：它们的绑定语义就是等待期，
不在这次层间不一致的范围内。也不新增任何生成 eligibility 的入口。

这个改动只让「AG-1 已经能给出、LR-2 本来能接收」的选项不再被中间层抹掉。

## 边界与不声称的事

- **没有证明自然可达性**。测试里的 OPEN eligibility 由测试层用 LR-4 自己的
  `ResumeEligibility` + `commit_records` 合成，并明确标注为合成状态；仓库里没有公开接口
  会给 PAUSED 活动发资格，本单也没有验证 AG-2 会在真实运行里生成这样一份资格。
- **不能解遗留实例**。现场那个 PAUSED 活动没有资格、没有有效的 World 目标、没有完成契约，
  本修复不给它制造资格，所以它仍然卡住；G1 仍未通过，V-C 恢复仍 FAIL。
- 不改落盘格式、不改对外接口、不新增依赖、不碰生产实例与产品模型。

## Alternatives considered

1. **维持原写集，只改入口一处，按「具体受阻」收尾。**
   最强理由：改动面最小，不碰 reconcile 的批量清理逻辑，回归风险最低。
   否决：入口先 reconcile 后读资格，旧行为下资格在 reconcile 阶段就被标 `STALE`，入口的
   状态断言根本轮不到；只改一处等于什么都没改，AG-1 给的选项仍必被拒绝。
2. **反过来收窄 AG-1，把 PAUSED 的 `RESUME` 选项隐藏掉。**
   最强理由：外层不动，LR-4 的契约保持原样，不需要放宽任何状态谓词，也避免「多一种可恢复状态」
   的语义扩张。
   否决：设计原文允许 PAUSED 回来，LR-2 也早已允许 PAUSED→ACTIVE；隐藏它会删掉一条既有合法
   路径，把层间不一致掩盖成「模型看不到就不会撞墙」。这是删能力，不是对齐。
3. **测试侧绕开：先 reconcile 再把资格重写回 OPEN，或猴补 stale 判定。**
   最强理由：业务代码零改动，测试也能绿。
   否决：真实入口仍然先 reconcile，绕过等于让测试绿而现场依旧拒绝，属于自欺。
4. **顺手把 condition / agenda 分支也放宽到 PAUSED。**
   最强理由：三处看起来是同一种「非 WAITING 即失效」的写法，一起改更整齐。
   否决：Condition 与 AgendaItem 的语义就是等待期绑定，PAUSED 不是它们的合法宿主；
   本次不一致只在 eligibility 这一条路径上。范围外扩会造出没有依据的新行为。
5. **新增无资格 PAUSED 的恢复条件或自动恢复。**
   最强理由：现场实例正是「PAUSED 且无资格」，若能直接恢复就能解卡。
   否决：设计原文只允许「各自恢复条件满足」后回来，没有给无资格路径授权；新增资格或自动恢复
   属于产品/契约决定，本轮不自行放行。

## Consequences

- PAUSED + OPEN（未过期、revision 与 episode 匹配）的资格在 reconcile 后仍是 `OPEN`，
  显式入口可以把活动从 PAUSED 恢复为 ACTIVE，并把资格置为 `CONSUMED`
  （`CONSUMED_BY_EXPLICIT_RESUME`）。
- 原来必被拒绝的 AG-1 选项现在能走通；但这不改变「谁负责生成资格」这一点，自然链仍未证。
- 清理逻辑对 PAUSED 上的 condition / agenda 仍然照旧失效，不会因为这次对齐而延命。
- 后续（2026-10-24，用户批准的新规则）：PAUSE 可以显式绑定一个本轮 offered 的条件，
  由 owner 满足后才生成 OPEN 资格，见
  [显式条件 PAUSE](../feature/2026-10-24-paused-explicit-resume-condition.md)。本笔记的
  「不新增任何生成 eligibility 的入口」仍是对该笔记当时范围的准确描述。
- 旧复现路径（`STALE` 之后入口报 `not OPEN`）成为回归测试的基线：把两处谓词还原成旧写法时，
  新套件 12 例中 3 失败 1 错误；当前树 12 例全过。

## Verification

- 新测试 `tests/test_paused_eligible_alignment.py`（12 例，WSL `/tmp/rc104-venv`，
  `python -B -m unittest -v tests.test_paused_eligible_alignment`）：
  真实 `ActivityAuthorityLease` / `CanonicalActivityStore` / `ActivityCommandService` /
  `WaitingCoordinator` 及其三个 store，隔离临时根；Decision/Adoption 两个读端口是测试层替身，
  `AdmissionIssuer` 的 grant/scope/fingerprint 校验真实执行。
  - 正控：PAUSED 上匹配 revision 的 OPEN 资格过 reconcile 后仍 OPEN，AG-1 合法集含 `RESUME`，
    显式入口恢复到 ACTIVE 且资格 `CONSUMED`；WAITING 对照仍可恢复。
  - 负控（各自独立场景）：stale revision、episode 不匹配（WAITING 路径，PAUSED 下 canonical
    `pause` 会清空 episode，该谓词不可触发）、ACTIVE 目标、terminal 目标、过期、`CONSUMED`
    不可复活、未知 reference、前台被另一 Activity 占用（`ForegroundConflictError`）、
    重复相同 idempotency 不产生第二次转换。
  - condition/agenda 同一 reconcile 内仍然 `STALE`/`CANCELLED`，只有 eligibility 分支放行。
- 修复前反例：在仓库外临时副本把两处谓词还原为 `!= WAITING`，同一套测试 3 失败 1 错误
  （旧行为下资格被标 `STALE`，入口报 `not OPEN`）；独立探针在旧写法下输出
  `reconcile invalidated 1 -> STALE` / `RESUME REFUSED`，在当前树输出
  `invalidated 0 -> OPEN` / `RESUME -> ACTIVE` / `CONSUMED`。仓库本身未回滚、未改动。
- 笔记门禁：tree / format / archive 三条脚本。
- 未验证：AU-1/AG-2 自然生成资格、遗留实例解卡、G1 任一场景、真实模型、24h。
