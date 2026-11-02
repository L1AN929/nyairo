# Agent Note: 历史影响选择实验的离线 harness

Status: implemented

## Problem

预登记（`docs/engineering/history-choice-preregistration-20261029.md`，本人已确认）要求
在同一新实例里真实采集、冻结场景、再比较两臂历史文本对选择的影响。但仓库里只有**机制**：
`evaluate_once(act=True)` 会一路走到内容/AR-0/Supply/M0，`evaluate_once(act=False)` 又会
改变 readiness 与候选，两者都不是「只要一个决定」的边界；请求额度虽然已经有一本持久账本
（`life_request_budget.py`），却没有任何地方能把「本阶段最多 8 次 / 影子正好 40 次 / 承接
最多 8 次，合计 60」变成一个**发送前**就成立的硬门；而预登记还要求两臂之间只差一个历史
文本字段，任何其它差异都必须阻止发送。没有这些，跑一次就会烧掉不可退的额度，还可能把
多变量比较当成单变量证据。

## Decision

新增 `scripts/history_choice_experiment.py`（CLI：`offline-check` / `prepare` / `run` /
`audit`）与 `tests/test_history_choice_experiment.py`。关键接缝如下：

1. **影子边界 = 正式 AG-1 + 正式 cognition adapter + 实验自己的 shadow store。**
   影子样本走 `AgencyDecisionService.evaluate_opportunity`（与生产 cognition shadow
   harness 同一条路：`AgencyDecisionStore(namespace="shadow")`、`AgencyDecisionAuthority`
   从 runtime 已持有的 lease 签发的 capability、`max_schema_retries=0`）。模型输出照常被
   `DecisionOutputValidator` 校验，DecisionRecord 只落在实验根，不采纳、不跑 writer/Action、
   不写 M0。
2. **干预只发生在 `prior_experiences[*].text`。** 候选、refs、合法集、情境、事实、参数全部
   从一次真实状态冻结；B 臂用一段固定中性句子按**等 Unicode 字符数、等换行位置**替换文本，
   refs/experience_id 原样保留。发送前用正式 adapter 自己的 `build_prompt` 渲染 payload，
   把历史文本归一成占位后比指纹；不一致就 `SECOND_VARIABLE_DIFFERS` 拒绝发送。
3. **额度只有一个来源。** 每次发送前读同一本 `LifeRequestBudget` 的 run window 计数；进入
   某阶段时还把 `budget.run_window_max` 临时压到 `已用 + 阶段上限`，所以「决策+内容」一个
   回合也不可能越过阶段上限，阶段结束再恢复（整批 60 仍在）。没有第二份计数、没有克隆
   账本。
4. **采集要同时满足「有 H」与「可冻结出 ≥2 个可执行选择」。** 每轮真实 `evaluate_once` 后，
   一旦 H（owner 新版本 + 正式 M0 可读）成立就尝试冻结 + 结构门；不可比就继续采集到 8 次。
   H 的对应经历必须真的出现在冻结历史里，否则按 `NO_EFFECTIVE_HISTORY` 结束。
5. **内容输出 cap 是实验参数 2048。** 生产 `_model_writer` 默认 512，harness 自己按预登记
   构造 `ModelContinuationWriter(..., max_output_tokens=2048)`；`offline-check` 用假
   transport 截获**最终 payload**，实测 `max_tokens/temperature/stream`，与预登记不符即
   `PREREG_MODEL_PARAM_MISMATCH`，不发任何请求。
6. **真实承接的 B 臂是真反事实。** 影子结束后把实例根整根复制到同层
   `<root>-branch-shadow-b`（跳过 socket/FIFO，实例文件里的绝对路径重写），分支自己的
   Supply owner 与 Memory 起在分支内；删除底账是唯一被允许「共享」的文件（既有规则要求它
   在业务根之外，且本实验不执行 DELETE），审计里单列。B 臂的决策与内容都只看到中性历史
   文本（与影子 B 臂同一个干预），A 臂才写主实例历史。

`offline-check` 把 HTTP `connect` 换成「一碰就炸」后跑四项门：26 例单元测试、参数实测、
真实 Supply owner + 正式 runtime 的结构门（0 请求、候选 2/2 可执行、NO_ACTION 合法）、
以及整条流程的脚本 dry-run（采集→冻结→20 对→A/B 承接，46 次替身请求，结论字段固定为
`DRY_RUN_SCRIPTED_NOT_A_RESULT`）。本窗口产品调用 0、未建正式根。

## Alternatives considered

- **`evaluate_once(act=False)` 或不 act 的「只决策」入口**：最省事，也完全复用生产链。
  但它会改变 readiness 与候选（预登记 §4 明确点名），`act=True` + gate off 又可能已经发
  内容请求并写 Supply；两者都不是单变量边界，因此否决。
- **直接复用「最后一次采集请求」当冻结模板**：实现最短，且模板一定来自真实一轮。但它的
  `prior_experiences` 早于 H 的经历被准入，H 的版本又已前移，影子提交时的 freshness 检查
  会把它判成 stale；冻结「现在」的状态才能既有 H 历史又保持候选 revision 一致，故否决。
- **每个影子样本复制一份账本（或每个样本一个 run）**：可以彻底隔离副作用，但会产生多份
  60 次额度、也会让「统一 batch cap」变成空话，预登记 §4/§8 明确禁止，否决。
- **采集一出现 H 就停**：最接近「达到最低历史门槛就停」。dry-run 证明它会死在
  「H 占着前台 → 只有 CONTINUE 合法 → 场景不可识别」上；预登记要求采集同时得到可冻结场景，
  所以改成「H 成立后每轮尝试冻结，不可比就继续到 8 次」。
- **B 臂沿用复制过来的真实历史**：最省实现，也是「同一实例的副本」。但那样 B 只是重复
  擦一遍真实历史，不再是反事实；预登记把它写作 counterfactual branch，所以仍施加同一个
  中性历史干预。
- **让内容沿用生产 512 cap**：与预登记固定的 2048 不符；预登记要求实测 payload 不一致就
  停下来问，所以 harness 显式按 2048 构造并把它变成硬门（不修改生产默认）。

## Consequences

- 收益：三个阶段、两臂干预、额度与身份门都在**离线**可执行且可复跑；真实运行时第一笔
  请求之前所有前置（参数、根、账本、可执行性）都已经用真实 owner/runtime 走过一遍。
- 代价与残留风险：影子每个样本单独构造一个 adapter（保证不共享响应缓存），schema 重试
  被设为 0；采样只按「一次请求一个槽」计，若将来 adapter 引入额外尝试会触发
  `LEDGER_DRIFT` 停批次而不是少算。
- 未验证：真实模型下的行为、8 次是否够、20 对是否出现差异，全部未做；dry-run 的替身
  结果不得当证据。删除底账在两个分支间共享（本实验无 DELETE），这一条在审计里是显式
  例外而不是「已证明完全隔离」。
- 真实承接阶段仍然可能 NOT_RUN（分支隔离审计、截止时间、新鲜决定 NO_ACTION/过期/无步），
  预登记允许这种结果，报告原样给出，不补样。

## 返修（2026-10-29 safety-finish）

**Problem**：初版的绿只证明了「脚本替身能走通」，没证明真实默认路径安全：`structural_gate` 默认
`transport=None` 会构造真实 provider（离线段靠调用方传替身掩掉了）；`run_shadow` 有 stop 仍会
无条件进承接；`run_continuation` 先跑 A 再 clone B，B 拿到的已是 A 的效果；采集/内容没有逐发送口
的截止与失败门；每臂请求数只有阶段额度、没有「1 决策 + 1 内容」。

**Decision**：结构门默认值改为本模块 `CaptureTransport`（合法 NO_ACTION，0 网络 0 预算）；
新增 `SendLatch` 挂在决策 adapter 与内容 client 两个发送口前，截止/供应商失败整批停，`run_shadow`
任何 stop 走 `skipped_continuation`（两臂 NOT_RUN、0 请求）；承接改成先 clone 再跑 A，活 sqlite 用
`sqlite3 backup` 取一致快照、目录权限随原实例、socket/FIFO 跳过，复制失败两臂都不跑；两臂同一
trigger、每样本独立 shadow store，发送前用渲染文本+payload 双指纹加历史形状拒裁剪与第二变量；
结构门要求第二个可执行候选来自另一件作品；`schema_retry_index>0` 当场拒绝。

**Alternatives considered**：只在 dry-run 调用处补传替身——能过检查但默认路径仍然会发真实请求，
正好是本次被抓的漏洞，否决；用 `shutil.copytree` 直接复制活实例——会复制到未提交事务的半截 sqlite，
否决；先跑 A 再判断能否 clone（原实现）——B 已不是同一冻结起点，预登记的反事实含义就没了，否决；
保留 schema 重试并让它再计费——多一次尝试就是多发一次请求，硬 cap 会被后验漂移掩盖，否决。

**Consequences**：收益是四条发送口（结构门/采集/影子/承接）在离线就各自可证 0 网络、0 越额；
代价是失败一律 fail-closed，采集遇到供应商失败直接收 partial、不补样，B 在截止或克隆失败时
NOT_RUN。未验证：真实模型行为、真实 provider 下的失败分类、跨机器克隆一致性；这些只能在
父级 prepare/run 时观察，自跑不算父级验收。

## 返修（2026-10-29 arm-ceiling）

**Problem**：safety-finish 之后「每臂最多 1 决策 + 1 内容」只剩后验检查：`run_continuation` 是
先跑完 A、再拿 `requests > 2` 判断要不要跑 B。也就是说第 3 次发送早就发出去了，才回头发现
超额；而承接阶段的 8 次上限是**两臂共享**的，A 花超了就是悄悄吃掉 B 的额度。预登记写死的是
「每臂一次新决定 + 一次内容」，事后才发现等于没有硬门。

**Decision**：新增 `arm_ceiling()`（与 `stage_ceiling` 同一做法）在 `continue_round` 里包住
`evaluate_once`：把两臂共用的那本 `LifeRequestBudget.run_window_max` 临时改成
`min(原总/阶段 ceiling, 本回合起点已用 + ARM_REQUEST_CAP=2)`，`finally` 恢复原值；不重置账本、
不新建分支账本、不退计数。决策与内容两个发送口本来就包着同一个 budget 对象
（`BudgetedCognitionAdapter` / `BudgetedModelClient`），所以任何第 3 次发送在
`LifeRequestBudget.reserve()` 处被拒，到不了 transport。A、B 各自取自己回合开始的计数作起点，
互不借用额度。`run_continuation` 的后验检查保留，但降级为断言（真触发就是硬门被绕过）。

**Alternatives considered**：`run_continuation` 里先跑 A、超额就不跑 B——就是原实现，问题正是
「发都发了才发现」，否决；给每臂建独立账本或独立 run window——会凭空多出两份 60 次额度，
预登记 §4/§8 禁止，否决；只在 `continue_round` 入口用 `assert_room(reserving=2)` 提前判一次——
能拦住「开局就没位置」，但拦不住一个回合里真的发第 3 次（例如未来 writer 多打一次内容），
不是发送前硬门，否决。

**Consequences**：收益是「每臂 ≤2」从后验事实变成 reserve 前就成立的拒绝，且与阶段/总上限是
同一条链路（min 语义，绝不放宽外层 ceiling）。代价是 `continue_round` 多了一个可变的账本
属性窗口，异常路径必须靠 `finally` 恢复（已用单测覆盖）；A、B 的 arm ceiling 值会写进回合记录，
便于审计对账。未验证：真实模型下承接回合的实际请求数（离线只证机制）、真实 provider 失败分类。

## A 路线 v2：自然采集的离线观察控制器（2026-10-29）

**Problem**：上面这套 harness 是「实验把主体叫醒去答一个问题」——`run_acquisition` 每轮用
`exp:acq:NN` 这个实验自己的 trigger 直接调 `evaluate_once`，再用「连续两轮零请求」提前结束整批。
它回答的是「两个候选里选哪件」，不是「她自己在原有规则下会不会动」。本人选定 A 路线后，需要的是
另一半：**只读地看**，推进完全交给生产 runner 原来的 due 门与 reconsideration 门；请求额度、观察
窗口、观察次数三件事分别到限；窗口内没有历史就如实报 `NO_EFFECTIVE_HISTORY`，有历史但没有
「H＋另一件作品」的合法可比场景就报 `SCENARIO_NOT_IDENTIFIABLE`，不把「没做成比较」说成「没检出
差异」。

**Decision**：新增 `scripts/history_choice_natural_v2.py` 与
`tests/test_history_choice_natural_v2.py`（29 例），运行手册
`docs/engineering/history-choice-v2-offline-runbook-20261029.md`。

1. **纯读观察器** `ReadOnlyObserver`：只读 Activity 状态、reconsideration 记录数、资格数、
   账本计数、wake 尾部；不取账本锁、不建目录、不调模型——为了这条，资格计数改成直接读 owner
   的状态文件，而不是走会建锁文件的 store 读口。**不做运行时全树扫描/哈希**（收尾时移除：
   全目录 SHA256 属于本项目禁止的反过度仪式，且并发 owner 的正常写入不等于观察器在写）；
   零写由离线测试用受控 read port ＋ 有限指定文件前后 bytes/元数据比较证明。
2. **推进只走生产 runner**：控制器每轮调 `assembly.runner_tick`（`maintain` → 事件轮询 →
   原 `run_due`）。观察序号不进入任何 trigger；`NOT_DUE`/`QUIET` 都是 0 请求，控制器只等。
   没有任何 `release_reconsideration`、改 TTL、写 head、注入事项/资格、强制动作的入口。
3. **三道门各在各位**：cap 8 落在 `LifeRequestBudget.reserve()`（持久、含决定与内容、失败不退、
   观察/重开/换时钟都不刷新）；`PlanGateLedger.assert_room` 只在发送前把拒绝说清楚；
   `HistoryDetector` 按机械规则认 H（owner 确认新版本＋该 artifact 的正式 M0 经历可读，
   checkpoint/receipt 不算）；`structural_gate` 复用正式 AG-1 请求与校验，底层是 v1 的离线
   transport（0 网络 0 预算），要求 H 可执行＋另一件作品可执行＋`NO_ACTION` 合法。
4. **真实入口是关的**：没有 `prepare`/`run` 子命令，`ProductRun` 一律
   `PRODUCT_RUN_NOT_AUTHORIZED`；`offline-check` 在 HTTP `connect` 硬断下跑测试、参数实测与两段
   scratch 演示（runtime/runner/owner/M0/门是真的，决策/内容 transport 与时钟是替身）。

**Alternatives considered**：

- **沿用 v1 的 `run_acquisition`（实验 trigger + 连续两轮零请求早停）**：最省代码，也已经被
  预登记验证过；但它正是 A 要删掉的东西——观察序号一多就可能变成「按实验节奏问模型」，而且
  「两轮没请求就结束整批」会把真实的静默旅程掐断。否决。
- **控制器直接调 `cycle.evaluate_once` 并自己决定何时问**：能把观察与请求解耦得最干净，但
  `runner_tick` 里的 `maintain`（LR-4 条件复查、到期判定）与事件轮询都是自然推进的一部分，
  绕开它等于自造一条「半生产」推进路径。否决。
- **让观察器用现成 store 的读口（如 `ResumeEligibilityStore.load_state`）**：语义最贴近生产，
  但实测中它在读的时候会创建锁文件，观察器就会被自己的零写自检抓住。改成直接读 owner 的状态
  文件（少一次锁，最多一拍旧），并不是「绕过 owner」，而是不做那次写。否决原方案。
- **在结构门上省掉正式 AG-1 请求、直接读候选行判可执行**：候选 record 里没有逐候选的
  `legal_decisions`，自己推一个「可执行」谓词就是新造规则。否决；继续走正式请求＋正式校验。
- **给真实运行留一个 `--dry-run` 之外的捷径（例如 `run --offline`）**：省事，但会让「离线」和
  「真跑」共用一个入口。否决：入口只有 `offline-check`，真跑一律拒绝。

**Consequences**：收益是 A 路线的采集侧现在离线可执行、可复跑，而且每条门都能指名道姓地被触发
（同状态 30 轮推进仍 0 请求、第 9 次发送到不了 transport、越界不观察不推进、观察器零写由受控测试
证明、H 只有机械规则认得出）。
代价是它**不保证能建起历史**：实测里脚本裁决 START 一次就真的写出版本、M0 可读、H 成立；下一次观察里那个真实新版本让
生产 runner 自己的 due/reconsideration 门**重新开放**（`NO_ACTION`，1 次请求），再下一次回
`QUIET`——没有人调 `release_reconsideration`。但那件占住前台后另一件只剩 `DEFER`，正式结构门
给出 `SECOND_WORK_CANDIDATE_MISSING`，整批以 `SCENARIO_NOT_IDENTIFIABLE` 收尾。这只是这个离线
样本实际走到的一条路径：不给真实模型下的概率，也不说明缺自然 head 更新源是结构门失败的直接后果。未验证：真实模型会怎么选、8 次够不够、窗口内能否建起历史、观察间隔与
窗口的真实抖动；20 对影子与真实承接本单没有重跑。真实运行仍需本人另批预算与授权。

