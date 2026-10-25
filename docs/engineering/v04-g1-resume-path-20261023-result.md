# 遗留 PAUSED 活动合法 RESUME 路径核对（2026-10-23）

结论先说：**当前代码里不存在让 `actv_91acbd742551228b` 这个 PAUSED 活动合法 RESUME 的路径**，而且原因不是"它起于旧 START"，是三层同时缺：没有候选、没有 eligibility、LR-4 对 PAUSED 也会拒。即便人为补出 eligibility，该活动保存的目标引用也进不了 World step 的命名空间，且没有 completion contract。本单只读核对，未写实例、未跑 runtime、0 产品调用。要动这条链属于改承接语义，按工单要求停在这里问。

## RESUME入口

先把工单里点的入口纠偏：`autonomous_life_resident.py:882-896` 调用的 `cycle.resume_selection()`（`autonomous_life_cycle.py:1730-1743`）选的是"这一轮继续哪件作品"（bound work / continuity history / latest resumable），**不是 Activity 的 RESUME**，和前台 PAUSED 活动无关。Activity RESUME 是另一条链，有四道门。

第一道是候选。AG-1 只对 `candidate_kind=RESUME_ACTIVITY` 且 `source_kind=RESUME_ELIGIBLE`、eligibility 非空的候选开放 RESUME（`agency_decision_ag1.py:3014-3035`）。该来源只读 OPEN 状态的 ResumeEligibility（`candidate_sources_ag0.py:1266-1270`、`1300-1309`），校验时排除 terminal 和 ACTIVE，PAUSED 可通过（`candidate_sources_ag0.py:1367-1373`）。也就是说 PAUSED 理论上能当 RESUME 目标，**前提是存在 OPEN eligibility**。

第二道是 eligibility 的来源。LR-4 只在一条 ResumeCondition 被事件满足后才生成 eligibility（`activity_waiting_agenda.py:2708-2719`、`2532-2578`）。ResumeCondition 是 WAIT 绑的（`activity_waiting_agenda.py:2253` 明确写 "Transition an ACTIVE/PAUSED Activity to WAITING and bind a new ResumeCondition"）。而 AG-1 只在 CURRENT_ACTIVITY 候选且前台 `ACTIVE` 时给 WAIT（`agency_decision_ag1.py:2989-3012`，WAIT 整块嵌在 CONTINUE 分支里）。模型对一个 PAUSED 活动连 WAIT 都选不了。

第三道是采纳。AG-2 的 RESUME 分支直接调 `waiting_coordinator.explicit_resume_from_eligibility`（`life_integration_ag2.py:3580-3642`），前置校验在 `life_integration_ag2.py:2703-2709`（eligibility 必须 OPEN）和 `activity_waiting_agenda.py:3040-3043`——**目标 Activity 当前状态必须是 `WAITING`，PAUSED 会抛 `EligibilityNotOpenError`**。注意 LR-2 自身的 `resume_activity` 是允许从 PAUSED 恢复的（`activity_continuity.py:2453`），但现有 AG-2 不直接走它。

第四道是前台冲突，本活动是前台自己，`fg_id == act_id` 这一条不拦（`activity_continuity.py:2144-2157`）。

所以"新鲜 Decision RESUME 一个 PAUSED 活动"在当前链上不成立：没有候选（没有 eligibility）、有 eligibility 也会被 LR-4 的 WAITING 前置拒。顺带记一个代码层面的不一致：如果该活动真有 OPEN eligibility 且前台是 PAUSED，AG-1 会给 RESUME 和 ABANDON（`agency_decision_ag1.py:3027-3045`；`tests/test_day_life_decision_legality.py:509-525` 也这么断言，本单未运行），但 AG-2/LR-4 在 3040 行会拒 PAUSED。这是静态路径分析，没真跑。

"重放旧 START"和"新决定 RESUME 已有活动"是两件事，本单结论只针对后者：即便将来补出合法 RESUME，也不该拿旧的 START Decision 去重放，旧的 Decision/Adoption 已提交且不可复用。

## 当前字段与前台

实例 `/root/nyairo-day-20261022`，只读投影：

- `canonical_life_state.json` revision 15，`foreground_activity_ref=actv_91acbd742551228b`。该活动 `activity_kind=GO_TO_OBSERVED_PLACE`、`status=PAUSED`、`checkpoint_ref=chk:stage:001`、`origin_ref=world_fact:place:home`、`last_action_ref=null`、`waiting_on_ref=null`、`resume_condition_ref=null`、有 `paused_at`。
- `subject_refs` 四条，全部是 `world_fact:` 前缀：`world_fact:stay:home:REST:300`、`world_fact:bodyact:pose:seated:south`、`world_fact:bodyact:pose:lying:south`、`world_fact:bodyact:pose:transitioning:south`。
- `activity_progress_state.json` 里，该活动的 result binding 与 progress contract 的 `target_refs` 同样是这四条 `world_fact:` 引用；`completion_contracts_by_activity` **没有这个活动的行**；`progress_state=UNKNOWN`、`completion_status=NOT_COMPLETE`。
- `waiting_agenda/eligibility/run` 与 `waiting_agenda/resume_conditions/run` 两个目录里只有 `.aggregate_io.lock`，没有记录文件。
- 其余活动：两条阅读 ABANDONED、一条收尾 COMPLETED；没有第二个 PAUSED/WAITING。

前台占用是具名规则，不是从名字猜的：`autonomous_life_cycle.py:5120-5131` 写明**只有 WAITING 释放槽位，ACTIVE 和 PAUSED 都占**；AG-1 侧同一条规则在 `agency_decision_ag1.py:2930-2933`（`foreground_holds_occupancy = cur_act and cur_status != WAITING`）。因此 PAUSED 期间：START 对新候选全部撤回（`agency_decision_ag1.py:2950-2962`），CONTINUE/WAIT/PAUSE/ABANDON 只在 CURRENT_ACTIVITY 且状态 ACTIVE 时合法（`agency_decision_ag1.py:2989-3012`、`3274-3287`），而 CURRENT_ACTIVITY 来源对 PAUSED 直接拒绝并给原因码 `ACTIVITY_PAUSED_REQUIRES_RESUME_ELIGIBLE`（`candidate_sources_ag0.py:1154-1155`）。结果就是本活动一个候选都没有，模型能选的只剩 DEFER 类不动状态的决定——**不是"还没试过让它 ABANDON/WAIT"，是现有词汇在 PAUSED 上根本不提供这两个选择**。这与 `autonomous_life_cycle.py:5126` 注释里"resuming it is her only legal move"的说法矛盾：注释承认只有 RESUME，但代码没给无 eligibility 的 PAUSED 任何 RESUME 候选。

World 目标事实这一条也要分清。RESUME 这个转移本身（LR-2）只需要活动记录和 revision，不需要目标；但候选层和 step 层需要。候选的 `target_refs` 由活动记录的 `subject_refs`/`target_ref`/`checkpoint_ref` 拼出（`candidate_sources_ag0.py:1391-1400`），而 step builder 只认 `place:`/`object:`/`stay:`/`bodyact:`/`dailyact:` 五种前缀（`autonomous_life_cycle.py:6337-6342`、`6418-6431`）。本活动保存的四条全是 `world_fact:` 前缀，`checkpoint_ref` 是 `chk:`，`origin_ref` 是 `world_fact:place:home`——都命中不了，`_candidate_is_world` 会判否、`_world_step_spec` 会抛 "names no observed World target"。`origin_ref` 里那个 `place:home` 不在 target 集合里，也不能被 step builder 读到。新鲜 World 观测只用来填 `place`/`world_revision` 做幂等键（`autonomous_life_cycle.py:6432-6434`），**不参与补目标**；同一轮新读到的 world opportunity 是另一个 START 候选，RESUME 选不上它。另外 World 的 adoption/step 只在没有前台时构建（`autonomous_life_cycle.py:5182`、`5264`），PAUSED 占着前台，这条路径这轮压根不出现。

所以第二个必须给结论的问题：**该活动不具备 step builder 需要的 World target 事实；这个缺口不能靠同轮的新鲜 World 观测自动补上，目标必须在原活动/候选里已指名，缺了就固定受阻**。依据是保存权威里的真实 ref 字段，不是构造默认值，也不是 root socket 缺失的推断。

## 方案与推荐

方案 A（按工单设想，用既有 RESUME 链做有界真场景）对本活动**不可行**：既没有 OPEN eligibility，目标也不是 WAITING。要让它成立，先得往实例里写一条 WAIT/eligibility，而模型选不了 WAIT（ACTIVE 才有），那只能 operator 手工造条件——超出本单、且是实例写。否决，不跑。请求额度余 4 也不需要动。

方案 B（最小修复现成接缝）方向上可行，但三条路都碰到承接语义：

- B1 对齐 AG-1 与 LR-4：让 `explicit_resume_from_eligibility` 接受 PAUSED（LR-2 本来就允许），修掉"AG-1 给 RESUME、LR-4 拒 PAUSED"的不一致。改动最小（1 代码 + 测试 + note），但对本活动无效——它没有 eligibility；也不解决目标事实。
- B2 给"前台 PAUSED 且无 eligibility"新增自我 RESUME 候选与合法性，绕过 eligibility。这等于改 AG-1 第 67 节"RESUME 必须有 OPEN eligibility"的契约，面更大。
- B3 在 PAUSE 采纳时绑定 resume condition/eligibility，让现有链以后可用。改的是 PAUSE 的语义（2–3 文件），事后对所有 PAUSED 活动生效。

而且无论哪条，本活动还差两块：World 目标怎么重导出（保存的 refs 是 `world_fact:` 命名空间）、以及它没有 completion contract（到达/停留都无法结算）。只补 RESUME 通道会造出一个"能 resume 但做不了事、也完不成"的假通路。

**推荐：停，不做代码修复，先让用户拍板产品规则**——"模型自己 PAUSE 的活动，之后能否由一个新 Decision 直接 RESUME；如果可以，PAUSE 时是否必须绑定恢复条件"。这是契约层决定，不是监工能顺手补的接缝。定了规则再开一份带离线正负控的窄工单（写集 1–3 文件，验收命令写明），并把 World 目标/完成契约作为同一工单里具名的第二个问题，别拆碎。实例保持原样：不删、不改 Activity、不重放旧 Decision、不新建 eligibility。

## 验收与边界

本单实际执行的只读命令（WSL，未 source 密钥，未起服务、未跑 runtime/owner）：

1. `find/ls /root/nyairo-day-20261022`：定位状态文件，EXIT=0。
2. python 只读投影 `canonical_life_state.json`（仅 id/kind/status/ref/enum）：EXIT=0，输出见上。
3. python 只读投影 `activity_progress_state.json` 的 binding/progress/completion 三段：EXIT=0。
4. `ls waiting_agenda/eligibility/run`、`waiting_agenda/resume_conditions/run`：只有 lock 文件，EXIT2=0。

代码侧只读了命中片段，没有通读目录：`autonomous_life_cycle.py`（resume 选择、前台占用、World adoption/step、guard）、`autonomous_life_resident.py`（baseline 的 resume_selection）、`candidate_sources_ag0.py`（两个来源）、`agency_decision_ag1.py`（合法决策矩阵）、`life_integration_ag2.py`（RESUME 采纳）、`activity_waiting_agenda.py`（eligibility 生成与显式 RESUME 前置）、`activity_continuity.py`（LR-2 转移）、`life_pause.py` 头部、`tests/test_day_life_decision_legality.py` 命中段。

场景分级，不混着说：

- **本单只读已证**：实例状态/revision/字段；eligibility 与 resume_conditions 存储为空；上述代码路径对 PAUSED 的四道门；保存的 target refs 命中不了 step builder 命名空间；该活动没有 completion contract。
- **前序父级已证**：状态读面能把 PAUSED 前台如实报出来（`v04-g1-state-surface-20261023-parent-result.md`）。
- **缺口**：没有合法 RESUME；没有目标事实；没有完成契约；AG-1 与 AG-2/LR-4 对 PAUSED 的语义不一致。
- **未证**：真实模型面对这种局面会选什么（根本没有 RESUME 候选，无法观察）；任何假设修复的运行行为。

边界：World 遗留活动即便将来恢复，也不等于阅读/作品续作，更不等于 G1 通过。operator pause（`life_pause.py` 的位文件，`one_day_life.py pause/resume` 清的也是它）与 Activity 的 PAUSED 是两个 owner 的两个开关，不能拿一个替另一个。本单 0 产品调用，账本仍 13，共同 14/96 余 82，本功能请求 cap5 已用 1 余 4，未刷新额度。

## 未验证

- 没跑 runtime、owner、服务或产品模型；没构造任何可能写库的 store。
- `activity_waiting_agenda.py:3040-3043` 拒绝 PAUSED 只在代码层确认，没有实跑这个拒绝。
- `tests/test_day_life_decision_legality.py:509-525` 关于"PAUSED + 自有 eligibility 仍给 RESUME"的断言本单没运行，只读了源码。
- 历史 START 的 Decision/step_path 具体内容没读，只读了活动与进度里的 ref 字段；不能据此断言当时模型为什么选了这组 `world_fact:` 目标。
- 实例的进程、lease 状态本单未复核（没碰实例，也不需要）；ledger 13 沿用父级与本单只读结论，未再读账本。
- 没有验证任何"修复后能恢复"的假设，也没有给出未经跑过的可执行脚本当证据。
