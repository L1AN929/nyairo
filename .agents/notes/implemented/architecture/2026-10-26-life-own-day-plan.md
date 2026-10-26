# Agent Note: v0.5 她自己排一天 —— 日计划与外部义务分域

Status: implemented

## Problem

0.5 要回答的不是「她能不能给自己留一条稍后」（那已经在
[v0.4 稍后事项与多项目](2026-11-02-v04-autonomous-day-later-and-multi-project.md) 里做了），而是：

1. **她排的今天没有地方可放。** `FutureItem` 只有一个 `due_at` 时刻，没有计划日，也没有「我把这件
   排进今天」这个动作；把 due 直接当「今天要做的」会把所有到期事项自动认成计划，她也就没有「看过
   待办、决定今天不排」这个合法的空结果。
2. **外部义务不能和她的自选混在一个域。** 义务她不能取消/改期/挪日子，写主体是 operator 或以后的
   World 适配器；塞进 `life_future_items` 会让同一个文档出现两个写主体，也会改写那个域明写的边界
   （「不是别人递给她的清单」）。
3. **review/question 占满 2 个名额时，无对象的到期事项进不了最终请求。** 已有正式反例
   （`docs/engineering/v05-due-option-competition-20261026.md`）证明这是末尾截断；「今天排定的事」
   需要一个不被这两类挤掉的位，同时不能扩大其它排序。
4. **「她今天做成了没有」不能靠猜。** 同一作品有历史版本不等于这次义务完成；必须绑定真实
   owner 回执（本轮或已持久、且能关联回这次 Decision/Action），否则就是在 operator 的文档里
   伪造完成。 同根重装配后，已完成的义务也不能因为拿不到本轮内存就说不知道、再发一次。

## Decision

**两份文档、两个写主体、一条最小正式协议。**

1. **`life_day_plan.json`（她自己的）**：`chiyo.life.day_plan.v1`，一条记录 = `plan_ref` + 业务日 +
   `item_ref`（既有 `fitem:`）+ `source=SUBJECT_DECISION` + `decision_ref`。**不复制标题/到期**，
   读的时候从 `life_future_items` 取。只支持排「今天」（+08:00 业务日，由注入的业务 clock 换算），
   一天最多 3 条，0 条合法。写绑定提交后的 Decision；同一 Decision 幂等，新 Decision 同一事项同一天
   不多建。取消/改期仍走既有 `future_item_actions`，计划只投影成 `ABANDONED`，不写回、不加终态。
2. **`life_day_obligations.json`（operator 的）**：`chiyo.life.day_obligations.v1`。本代码里的写入口
   只有 `scripts/own_day.py`；Life 拿到的 `DayObligations` reader 没有写方法（这只约束本代码路径，
   不据此断言文件在操作系统层面不可被外部进程修改）。义务必须绑定一个现场可读、可续写的
   `artifact:` 目标和一个同目标的完成依据（`ARTIFACT_STEP_RECEIPT` / `SUCCEEDED`）；未知/不可读/
   不可续/依据指向别的对象一律具名拒绝。没有「标题即动作」的兜底，也不新增学校/打工动作。
3. **决策协议**：AG-1 新增可选顶层键 `plan_today: {"item_ref": "fitem:..."}`，形状由 day-plan 域校验；
   `DecisionRecord` 新增 `day_plan_request` 并在重建时重校验。日期不是模型输入。cycle 在 Decision
   提交后调 `DayPlan.plan(...)`，逐条报告 `APPLIED`/具名拒绝。
4. **预留位**：`build_options` 先按原顺序 review → question → due 截到 `limit`（一字不改），再把
   最多 1 条今日自选 + 1 条今日义务追加到末尾，有效上限 `limit + 2`。预留位仍是 `popt:` 个人机会，
   带 `kind`/`basis_ref`/`work_ref`/`dedupe_refs`。选中带 `work_ref` 的预留位 = 继续**它绑定**的
   作品，不是新建项目；没有 `work_ref` 的自选仍按普通项目选项。
   去重不只在 `build_options`：`ArtifactContinuationSource` 在最终 AG-0 候选层按“同一真实可执行
   目标 + 同一继续动作”再折叠一次。已存反例（义务候选与普通 artifact continuation 同指
   `seed_artifact`）现在只留带义务标签与 `oblig:` ref 的那条；`closeout:` 是不同动作、别的目标
   也照旧保留。折叠只认预留位给出的 `work_ref` / `dedupe_refs`，不按 `artifact_id` 全局删。
5. **仍然过既有门**：目标 PAUSED/WAITING → `PAUSED_TARGET_REQUIRES_RESUME`，不额外 START；目标不可
   读/已归档 → 该预留位撤回并具名；没有可执行义务就空位，不造兜底。
6. **结果从真实记录投影**：本轮的真实 `StepReceipt`（`APPLIED` + 同目标）绑定 `DONE`；失败/UNKNOWN
   记 `UNKNOWN`，没有回执是 `PENDING`。 同根新装配后不靠内存：只读扫描已存在的 AR-0 Action、
   AG-1 Decision 和 owner 回执，要求 Action 目标同一、`decision_ref` 是 `START`、其
   `selected_candidate_ref` 正是该义务确定性预留位会生成的候选 id，才重报 `DONE`。只有同作品
   版本增加、另一个 Decision 的动作、另一个目标都不认领；同一目标上无法证实归属的动作记
   `UNKNOWN`，不当作可安全重放的 `PENDING`。 全程不写结果 store、不改 operator 文档、不新增
   MISSED 或日终顺延。
7. **安静仍然是安静**：`life_step_readiness.SITUATION_KEYS` 新增 `day_plan` / `today_obligations`。
   计划或预留集合变化 = 新处境；同一集合继续 QUIET。**未到期的稍后列表不进指纹**：它还不可开工，
   放进去会让每次提案都多花一次请求；计划只在事项自己到期后才供给预留位。时间流逝本身不改变指纹。

## 兼容与代价

- 两份文件都不存在 = 旧行为逐 ref 相同；不迁移旧实例，不升 `life_future_items` 的 schema。
- `future_item_actions` 收紧为只接受 `fitem:` ref（`oblig:` 等具名拒绝）。这是原 docstring 已写明的
  合同（「cannot reach any other owner's object」），此前只是没拦住带冒号的 ref。 兼容性已逐项核过：
  裸 id 仍补成 `fitem:`、`fitem:` ref 原样接受、旧 `FutureItems` 记录本就是 `fitem:`、旧 Decision
  里保存的 `fitem:oblig:...` 拼写重建仍通过；被提前拒绝的只是原本会拼成 `fitem:oblig:...` 再去
  `NOT_FOUND` 的其它 owner 引用，没有改变任何原本成功的输入或重放。
- 计划文件损坏/不可读是具名错误，不当空表；义务文件同理。读故障不阻断回合，但会在报告里出现
  `day_plan_error`。
- 结果重读只覆盖同一实例、同一 PID 的有序关闭/重装配，不是 crash/强杀/半写/跨 PID 恢复；关联依赖
  AG-0 给预留位候选的确定性 id，公式若变旧结果会诚实落到 `UNKNOWN`。
- 只支持「今天」：未来日排程、日历、提醒、手机分栏都不在本单；义务不能取消/改期是结构性事实，
  不是提示词约束。

## Alternatives considered

**A. 在既有 `FutureItem` 上加 `class` + `planned_for` + `origin=EXTERNAL_GIVEN`。**
最强理由：省一份文档、复用既有幂等键与取消/改期写口，读端也只需要一个 store。
否决原因：同一个文档会出现两个写主体（她/外部），取消与改期权限只能在领域里按 `class` 分流；
它还会改写那个域的边界（「不是别人递给她的清单」），并把「外部义务」伪装成「她的稍后」。若升
schema 版本，旧读端会直接拒绝文件；不升版本则同一 v1 里塞两种语义，读端更容易混淆。

**B. 只加预留名额，不定义计划/义务。**
最强理由：改动最小，直接解决 review/question 挤掉到期事项的问题。
否决原因：任务要的是「她自己排今天」和「义务/自选可区分」，一个无名额无法支撑分栏、无法表达
「排了/没排」，也无法阻止她借 `future_item_actions` 取消义务。名额问题只是本功能的一个下游症状。

**C. 让模型直接写 JSON 或让 Life 在收尾时写 MISSED/顺延。**
最强理由：看起来能最快让「今天」页有内容。
否决原因：模型不能成为新的写主体（义务必须 operator 给）；日终 MISSED/顺延是新状态机，未被
批准，也容易把「没做」变成系统替她下的结论。本单只做「计划/执行/只读结果」。

## Consequences

收益：她可以显式把既有待办排进今天；operator 的义务与她的自选在数据上可区分；两条预留位不被
review/question 挤掉；同一真实目标不会以两个等价可执行入口出现；「完成」只在真实 owner 回执下
成立，并且能在同根新装配后从既有 Action/Decision/回执重新读回。代价是多一份文档、一个 operator
入口、一个 AG-1 键、一次预留位构造和一次只读结果重扫；手机「今天」页与跨日排程留给后续。

验证：`tests/test_v05_own_day.py`（58 例，含两次真 owner 正式链与同根重装配）与
`scripts/v05_own_day_smoke.py`（15 项检查，SMOKE_OK）在本仓库通过；详细实测与未覆盖边界见
`docs/engineering/v05-own-day-20261026.md`。Decision 与内容回答是脚本替身，不证明真实模型自然采用。

## 手机只读入口（2026-10-26 增量）

`GET /api/phone/today/arrangements` 只读投影今天的两域，不改变上面的写主体与决策：

- 自选计划读 `DayPlan` 并按所引事项的当前状态投影；`PLANNED` 只表示「排进今天」，
  不是「未开始」。执行状态只在持久 Action/Decision/owner 回执证明同目标、`START`、
  该计划确定性 `popt:` 候选均成立时为 `DONE`，否则 `UNKNOWN`。
- 义务结果复用 `persisted_obligation_evidence` / `obligation_result_view` 的既有规则；
  `PENDING` 是「还没有回执」，不是 MISSED 或失败。
- 今天的 Activity 只有被同一安排的精确候选 `START` Decision（经 AR-0 Action）关联时才算
  「已排」；关联不足进 `unclassified_activities`，不硬算「未排」。
- 日期取实例业务 clock（+08）；来源不可读是具名 503。读面零写，不建结果 store，
  不新增日终顺延/放弃/MISSED。
- 权限：loopback + 既有 host/origin 守护。`chiyo_phone` 工具的 `READ_ACTIONS` 尚未包含
  这个路径（现有 `/today` 也没有），所以她目前还不能经模型工具读取；接工具注册属于
  另一个全局协议改动，未在本增量内做。

验证：`tests/test_phone_own_day_read.py`（11 例）与 `scripts/phone_own_day_read_smoke.py`
（10 项检查，SMOKE_OK）通过；原 `tests/test_phone_today.py` 5 例保持。详见
`docs/engineering/v05-phone-own-day-read-20261026.md`。
