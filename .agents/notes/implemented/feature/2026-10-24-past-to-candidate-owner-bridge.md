# Agent Note: 过去已开始的活动 → 候选自带 owner 目标 → 下一步准备

Status: implemented

## Problem

v2 第 18 章第 3 节第一段要求「过去到候选」：不只把以前发生过的事写进 prompt，还要说明它改变了
哪些可见动作、准备条件、判断标准或方法。核对现有数据通路后，过去进入候选的两条路已经有真实
owner 链：M0 经历 → 实践回顾选项（候选集合）与 C 的
`CONTINUE_FROM_OWNER_CONFIRMED_OBJECT` → `_read_resume_point()`（本轮续作对象）。但**已经进入
候选的 owner 目标没有被正式执行消费**：

- LR-2 的 canonical Activity 与 AG-0 的 `CURRENT_ACTIVITY` 候选都带着 owner 自己写下的
  `subject_refs` / `target_ref` / `checkpoint_ref`
  （`candidate_sources_ag0.CurrentActivityCandidateSource.materialize`）。
- `_named_work_ref()` 只看候选的 `source_ref`；对 `CONTINUE_CURRENT` 那是 activity id，不是作品，
  于是返回 `None`，step 退回本轮绑定/历史选中的 `resume_point`。
- 当轮选对象（绑定的另一件作品，或历史选中的另一件）与 Activity 的目标不同，`CONTINUE` 会把
  下一版写到**另一件作品**；round 报告的 `step_work_target` 与真正执行的 step 也可能指向两个对象。
- 同一件作品还会以 closeout 包装同时出现（`closeout:artifact:artifact:<id>` 与普通
  `artifact:artifact:<id>`），收尾候选的 ref 含作品 id 子串，冒烟替身按子串选择时会被它抢先。

最小探针（同一 Activity 候选 target=A2、绑定作品=A1）在修复前输出
`step artifact: A1`，即候选自带的 owner 目标被丢弃。

## Decision

只补 **owner 记录 → 候选 → 下一步准备** 这一条窄桥，不新增候选种类、owner、落盘或资格：

1. 新纯函数模块 `past_candidate_bridge.py`：从候选的 `target_refs` 或 Activity 的
   `subject_refs`/`target_ref`/`checkpoint_ref` 中投影出第一个 `artifact:` 作品 ref；
   `artifactversion:`、`closeout:`、`place:`/`world_fact:` 等其它命名空间一律不认；阅读材料与
   私人事项文档（本实例的 `artifact_exclusions`）一律排除。它不读 owner、不造 ref、不判断
   可执行性，无作品 ref 时返回 `None`。
2. `AutonomousLifeCycle._named_work_ref()` 对 `CONTINUE_CURRENT`/`CURRENT_ACTIVITY` 候选走这条
   投影。正式 `_work_step_spec_for_decision()` / `_work_adoption_spec_for_decision()` 因此以
   **候选自带的 owner 目标**为准；owner 的 `resume_point` 仍决定它是否可续，读不到/已归档由既有
   `WORK_NOT_CONTINUABLE` 具名拒绝，不静默改指到别的作品。
3. `round_point_for()` 改用同一个 `_named_work_ref()`，所以 round 报告、`step_work_target` 与真正
   执行的 step 不可能指向两个对象。
4. AR-0 step 的启用条件增加「前台 Activity 自带作品 ref」一项：即使本轮绑定/历史选中的工作
   为空或不可续，running Activity 自己点名的作品仍能得到正式 step；可执行性仍由 owner 与 AR-0
   准入决定。
5. round 记录新增 `foreground_work_ref`（只读投影，报告用）。

明确不改：QUIET predicate/指纹/TTL、候选种类/来源种类、AG-1 合法集、AG-2/LR-2/LR-4 规则、
Memory/Supply 合同、资格生成/消费、前台规则；不自动恢复、不提交 Decision/Action、不把过去
当写权限。

## Alternatives considered

- **让 C 的历史选中对象覆盖 running Activity 的目标。** 最强理由是「过去真正改变下一步」。
  否决：Memory 里的字符串不能压过 owner 的 canonical Activity 目标；当两者不一致时覆盖会把
  版本写到 Activity 从未点名的对象，正是本单要修的错。
- **新增一个「过去候选」source kind。** 最强理由是职责单一。否决：AG-0 注册表按 source kind
  一对一，新增种类是契约扩张；而且目标对象本来就在现有候选里，缺的是消费，不是供给。
- **只改 step，不改 `round_point_for`。** 改动最小。否决：round 报告与执行目标会继续分叉，
  验收看到的 `step_work_target` 仍然可能是另一件作品。
- **在 assembly 里默认自动接 C/经历读口（修 plugin 路径）。** 最强理由是让每个正式入口都读到
  过去。否决：既有笔记明确把 Memory 边界与 C 读口的构造放在 resident（它持有该端口）；
  在 assembly 默认构造会改变「显式 None = 不接记忆」的对照语义，属于另一个契约决定。
- **把阅读材料也当作品继续。** 否决：阅读有自己的 step 与停点；本桥排除阅读/私人事项，
  避免把阅读目标误当作品。

## Consequences

- 一个由过去 Decision 启动、仍在前台的 Activity，其 `CONTINUE` 现在稳定写回它自己的作品；
  轮选绑定/历史对象只在候选没有作品目标时作为 fallback。
- 纯历史证据仍然真实：同一基线下，接 C 读口时 `_read_resume_point().selected_by` 为
  `CONTINUITY_HISTORY`、`history_state=OK`，不接时为 `BOUND_WORK`；本单声称它改变本轮
  **选中的继续对象**，不声称它压过已提交候选的 owner 目标。
- 边界：已归档/不可读目标按名拒绝；阅读/私人事项不会被投影成作品；无作品 ref 的候选保持
  原 fallback；不证明真实模型采用、G1、24h 或长时恢复。Decision/内容模型仍是脚本替身。

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q tests.test_past_candidate_behavior`
  → 17 例 OK：纯投影正负控（版本/其它命名空间/排除项/空记录）、候选目标胜出、无目标保持
  fallback、阅读材料不被当作品、已归档目标具名拒绝、仅版本 ref 不改变 fallback；脚本替身
  按整个作品 ref 选择（closeout 排除、反转顺序、不同 id、裸 id 归一、合法性缺失）。
- `python3 -B scripts/past_candidate_behavior_smoke.py` → exit 0：真实 Supply owner、真实 M0、
  真实 LR-2 Activity、正式 assembly/AG-0/AG-1/AG-2/AR-0；真实 START 后种子版本 1→2→3，
  另建的绑定作品保持 1；round 2 的 `foreground_work_ref` 与 `step_work_target` 都是 Activity
  自己的种子，而 `artifact_selection` 仍是 `BOUND_WORK` 的另一件（正是修复前会写错的位置）。
  历史对照：`CONTINUITY_HISTORY`/`history_state=OK` vs 无历史 `BOUND_WORK`。
- 受影响回归：`tests.test_v04_autonomous_day_stage` + `tests.test_single_world_candidate_guard`
  + `tests.test_day_life_decision_legality` → 38 例 OK；`tests.test_life_v1_integration.
  DContinuityIntegrationTest` → 9 例 OK；`tests.test_v04_work_revisit_runtime` → 1 例 OK。
- 未跑全库、未调真实产品模型、未启动任何既有实例；产品调用 0。真实模型是否采用该路径待父级
  另开新 root 有界验证。

相关：[有效搁置候选供给](2026-10-24-eligible-parked-candidate-supply.md)、
[wait-closeout](../feature/2026-10-28-v04-wait-closeout-core.md)、
[lived-day core continuity](../architecture/2026-11-13-v04-lived-day-core-continuity.md)、
[评价承接到同一作品后续实践](../../../../docs/engineering/v05-practice-continuation-20261025.md)（2026-10-25：
真实链里另有可续作品 B 且本轮绑定改到 B，每一步仍写候选自带的 A、B 版本不变；这条规则
没有被改优先级或选择算法）。
