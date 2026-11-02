# 暂停之后怎么回来：现有两道门、可修项与三条路线

日期：2026-10-30　性质：只读方案，未改业务、未跑测试/服务/模型　预算：141/300 不变

## 结论

- c现场可确认：第13轮含CONTINUE，第23轮已不含CONTINUE且提供WORK_HEAD_AFTER_NO_CHANGE；随后PAUSE，采样中条件PENDING、资格0。具体无步骤原因未据现场完整还原，不能简化成“没新head所以不能继续”。
  本次限定核查**没有找到**一条已定义、可直接解开原A 的漏接。
- 但也不能说「两门都按设计、没有任何漏接」。我只核了限定的调用链，仍有一个具名不一致：
  暂停视图的提示文案还写着「保持前台」，与 2026-10-24「PAUSE 释放执行槽」的规则相反。
  它是提示文案，不是机械断链；是否影响模型选择，未证。
- 「等自己的作品出新版本」如果确实没有独立更新者，就存在**自依赖等待风险**。但 waiting_on_ref 是哈希，
  不能反推具体作品；本作品的写入口也没有穷尽，c 的具体因果未完全还原，不能写成「永久死锁」。
- 本次比较保留既有合同、设计前提失效后的重考、沿用现成park/body三条路线，不声称穷尽。后两种现成条件静态可达，不等自然采用已验；新重考尚未形成可直接实施的完整规则，不能据此改旧c。

## 两道门现在实际怎么工作

1. **作品门**：`life_step_readiness` 在 artifact 目标上读 `no_change`，同一 revision 命中即返回不可执行；
   AG-1 据此撤掉 CONTINUE。解除只有三条：owner 真出新 head、记录超过 6h TTL
   （`life_step_readiness.py:365`）、显式 release。
2. **恢复门**：AG-0 只把 LR-4 的 OPEN 资格列为 RESUME 候选，PAUSED 本身不产生候选。条件
   PENDING→SATISFIED 仍靠 LR-4 的原事件判据；时间过去只让 readiness 的旧事实可能失效，
   **不会**产生事件、不能自动 OPEN。
3. **条件只有一个来源**：`wait_basis`，按「work/reading 依赖 → 身体 → 自选时间」fallback
   （`autonomous_life_cycle.py:5949-5956, 6272-6279`）。`pause_condition_view`（2098-2144，调用点 3611）、
   `known_waiting_basis_ref`（6399）、`pause_condition_spec`（6296-6310）都吃这同一个值；
   AG-1 用等值校验 hint（3375-3396），AG-2 用同一 spec 绑定（3836-3859）。
   所以**视图与正式采纳同源**，不存在「视图里没有、正式链另有其它路」的已证分歧。
   身体/自选时间也确实能成为 PAUSE 条件（见路线 C），不是 schema 摆设。

## 路线 A：保留已批准合同，不动语义（默认推荐）

- **最强理由**：c 现场没有已证的可修漏接；显式条件 PAUSE 是本人 2026-10-24 批准的规则，
  条件满足必须由owner事实证明。既有恢复接线的确证漏接可以修；另造未满足原条件时的恢复资格则属于新语义，不能混称修bug。
- **代价**：若依赖确为同作且没有独立更新者，按当前条件可能持续等待；这是待核的自依赖风险，不是已证永久不可恢复。
- **唯一可修项（待验证、不施工）**：`pause_condition_view` 的 docstring 与 `how`
  （`autonomous_life_cycle.py:2104, 2127-2129`）仍写 PAUSE「keeps the foreground」，
  与释放前台规则矛盾。父级判断：只把错误提示对齐已批准的释放规则，属于既有机制的一致性修正，可按当前离线授权推进；不借此改变资格、排序或承诺选择改善。
- **写集（仅此项）**：`components/life/scripts/autonomous_life_cycle.py`（2098-2144 文案）。
- **测试**：`tests/test_paused_explicit_condition.py::test_a_real_pause_binds_one_owner_checkable_condition_without_transitioning`（存在）。
- **最小验收**：若批准，只跑该文件现有用例，确认文案不再宣称保持前台；本单不跑。
- **a/b/c 可比性**：状态转换规则不变，但提示内容会变化，后续行为实验必须标注，不能宣称模型输入完全可比；不迁移旧根、不倒写。

## 路线 B：新增「依赖前提失效后的重新考虑」（proposed，须本人裁定）

- **必须先定义**：什么事实让 PAUSED 重新可选（不是「时间到」本身）；谁证明（LR-4 现无 owner 事件可等）；
  产出什么（只发一条 OPEN 资格，仍要新鲜 Decision 选 RESUME；还是只允许重新考虑一次）；
  去重、失效、预算、与 no_change/单前台/不自动恢复的边界。
- **最强理由**：她「这次不写」的判断本身就只有 6h 寿命（`life_step_readiness.py:365`）。
  把一条已过期的判断当成永真依赖，等于把临时事实变成死结；到期后重新问一次，恢复的是
  「已经问过」的语义，不是替她决定。
- **否决风险（明确）**：这正是 2026-10-24 note 已否决的「计时器=条件满足」边界。现有
  PENDING→SATISFIED 只认 EXTERNAL_RESULT_EVENT 精确匹配（前次扫描记
  `activity_waiting_agenda.py:1748-1755`，本次未逐行复核），TTL 过期不产生事件。
  落地需要新增事件语义 / 条件参数 / 来源区分（「agency 自己的记录过期」不是 owner 报告），
  **单独一个 reason_code 不够**；这些设计点目前都是未定项。
- **写集（若批准）**：`life_step_readiness.py`（暴露记录到期事实，不改 TTL）、
  `life_wait_basis.py`（识别前提已过期并给出新来源标记）、
  `activity_waiting_agenda.py`（新判据/资格或重考，仍走原资格 store）、
  `autonomous_life_cycle.py`（maintain 触发点）。
- **测试**：在 `tests/test_paused_explicit_condition.py` 增新 case（同 head + TTL 未到 = 0 资格；
  TTL 过 = 按裁定产出一条资格或一次重考；不自动 RESUME）。
- **最小验收建议（不运行）**：`python -m unittest tests.test_paused_explicit_condition tests.test_paused_background_single_execution`。
- **a/b/c 可比性**：改资格/重考语义，之后的运行与 a/b/c 不可直接比较，必须标注策略版本；
  旧根不迁移、不倒写。

## 路线 C：沿用现有 park/body 条件（正式链已可达）

- **事实**：`wait_basis` 在没有 work/reading 依赖时会退到身体，再退到自选时间
  （`autonomous_life_cycle.py:5950-5956, 6272-6279`），并原样进入 `known_waiting_basis_ref`
  与 `pause_condition_spec`。所以这条不是「schema 有、链没通」，而是**已经能绑**。
- **最强理由**：不新增任何规则。下一次她可以在暂停时选「等到某时刻」或「等身体缓过来」，
  而不是等自己写出新版本。
- **代价**：救不了 c 的原A——第23轮有 work head 依赖，优先序在 park/body 之前；
  park 要求同一条回答里 `later_item.park_current=true`；body 要求真有限的新鲜身体读和 ACTIVE 前台；
  真实模型会不会自然选它们，未证。
- **写集**：无。若想让它更常出现，是 offer 优先序/提示词调整，属产品行为，须另批。
- **测试**：`tests/test_paused_explicit_condition.py::test_a_self_chosen_time_condition_is_evaluated_only_at_its_explicit_instant`（存在）。
- **a/b/c 可比性**：不改代码，可比。

## 未核与边界

- waiting_on_ref 哈希不可反推，具体绑到哪个 artifact、是否有独立更新者，均 UNKNOWN。
- 第13轮是流程 NO_ACTION、Decision 为空，不能说模型明确拒选 CONTINUE；第23轮去 CONTINUE 的
  现场只有合法集与条件 offer，no_change 的执行因果未现场确证。
- 条件是否在发送前原子快照中被模型看到：send_status=UNKNOWN。第109轮 DECISION_FAILED 细因未核。
- body/park 的「已可达」来自静态调用链，未用真实模型验证自然采用。
- 本单 0 产品调用、未动旧根、未跑测试服务，141/300 不退款。
