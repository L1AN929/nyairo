# v0.5 自主未来事项与长期作品入口只读核查（三问）

范围：只读定位「她能否自己创建未来事项」「长期作品/项目能否自己推进」「断在哪」。
本次没有运行测试、实例、服务或产品模型，没有改业务源码，没有碰旧 root。共同预算仍按
94/300、余 206 沿用，不是本单新增调用。所有结论来自当前工作树源码片段与既有 note/report，
不代父级声称已验证。

本地 HEAD：`17f69e22ec3959c20de8228aebbf3766494cc8a8`。分支与远端未动。

## 先给结论

1. **能自己创建，不是 operator-only。** 模型可以在 Decision 输出里带一个可选 `later_item`；
   提交后由日循环写入她自己的 `life_future_items.json`。但「创建」只是记下一条 ≤200 字标题 +
   一个到期时刻的 OPEN 条目，不是任务、承诺、日历或目标。
2. **长期作品能自己推进，但要分成两条不同的路。** 已有作品通过 artifact continuation 候选续作，
   步骤跟着 Decision 点名的作品走，不可续就具名拒绝；新项目通过 `popt:` 选项触发一次真实 owner
   创建。两条路都有正式真 owner 证据，但「跨轮的长期自主计划」没有因此被证明。
3. **不是压根没有能力。** 主链已有脚本Decision＋真owner历史证据；本次另列3个静态疑点，分别涉及有界选项排序、park语义一致性、备注接受后未持久化。没有新运行反例，不把三者统称已证bug或本次真实日的原因。普通日子NO_ACTION合法，不因此制造保底事项。

父级只核了交付结构、引用文件存在性、与既有note/report的一致性，未独立逐行审业务源码；下文行号为worker静态定位。当前装配现场可用性与真实自然采用未新验。

---

## 问一：能否自己创建 future item，还是 operator-only？

**结论：已找到正式自主写入口「模型提案 → AG-1验证 → Decision提交 → 日循环写store」，不是operator-only。** 本次扫描未找到operator专用创建CLI；不据此断言全仓或仓库外没有其它入口。手工改JSON不是本报告建议的操作，也不是owner正规写入证据。

### 完整路线（具名锚点）

1. **提示暴露**：`agency_cognition_adapter.py:122` 的 `SYSTEM_PROMPT` 在可选键里列出
   `later_item` / `future_item_actions`；具体形状与用法在 `:241-255`。`park_current` 的规则
   在 `:245-249` 写得很窄：只有处境说可以 park、且 `decision` 是 WAIT（或对应的显式条件 PAUSE）时，
   才允许把同一时刻带上去。
2. **可选键与验证**：`agency_decision_ag1.py:3110-3135` 的 `allowed_top_keys` 把
   `later_item` / `future_item_actions` 列为全局允许的顶层键；`:3414-3441` 调
   `life_future_items.validate_proposal` / `validate_actions`，失败即
   `INVALID_SCHEMA`，不会静默丢弃。验证后的字段进入 `DecisionRecord`
   （`:2124-2125`，重建时再验一次 `:2271-2289`，构造在 `:5164-5165`）。
3. **正式消费**：Decision 提交后，日循环调 `_apply_future_item_writes`
   （`autonomous_life_cycle.py:4246-4303`，调用点 `:6002`）。它逐条报告
   `APPLIED` / store 自己的拒绝；`propose` 用本轮 `observed_at` 解析相对延迟，重放不漂移。
4. **持久写**：`life_future_items.py:435-478` 的 `FutureItems.propose` 写一条
   `fitem:<hex>`：标题、`due_at`、`source_refs`、`origin`、`activity_ref`、`workspace_id`、
   `status=OPEN`、`revision=1`、`times_considered=0`、幂等键。上限与边界在 `:67-73`：
   延迟 1 分钟～14 天、标题 ≤200、来源 refs ≤6、同时 OPEN ≤24；容量满时
   `FUTURE_ITEM_CAPACITY_FULL`，不静默覆盖。
5. **后续处境**：`open_items()` 进 `decision_situation_context.later_items`
   （`autonomous_life_cycle.py:3566-3570`，最多 6 条）；`due(now)` 进本轮到期集合
   （`life_future_items.py:428-431`、`:561-581`）；到期的条目按有没有对象分流（见问二）；
   `due_items` / `project_options` 进入处境指纹（`:5134-5136`），集合变化才重新问。
6. **operator 预建**：`autonomous_life_assembly.py:927`装配`FutureItems(root, subject_id=...)`。worker检索未找到另一个正式创建CLI/工具或`ORIGIN_MATERIAL`生产者；这是检索范围内未找到，不是全仓、部署外脚本均不存在的证明。可手工调用库不等于产品恢复或创建接口，也不是本次已执行操作。

### Decision 类型与裁剪

- AG-1 的 `allowed_top_keys` 是全局集合，**没有按 Decision 类型收窄** `later_item` /
  `future_item_actions`。任何成功提交的 Decision 都可能在解析层带上它们。
- 但 `park_current` 的语义只在 WAIT / 显式条件 PAUSE 路径生效；代码没有在 AG-1 强制
  「decision 必须是 WAIT/PAUSE」（见问三缺口 2）。不带 `park_current` 的 `later_item` 只是
  一条给自己的备注，不产生任何动作（提示 `:250` 自己写明）。
- 提示层的裁剪：`agency_cognition_adapter.py:1069-1092` 把 `later_items` 展示上限设为 6、
  `due_item_refs` 6、`project_options` 4；预算不足时 `:899-908` 先把 `later_items` 截到 3 条，
  更靠后的 `:945-951` 会把 `later_items` / `due_item_refs` / `project_options` 整块丢掉
  （裁剪顺序 `:1009-1028`）。这是有界设计，不是写入裁剪；但超预算回合里模型会看不见较后的
  条目与到期项。

### 合法 JSON 结构（合成示意，不是完整提示）

```json
{"decision": "WAIT",
 "later_item": {"title": "回头把这一页读完", "in_minutes": 90, "park_current": true}}
```

```json
{"decision": "NO_ACTION",
 "later_item": {"title": "下周把草稿再看一遍", "due_at": "2026-11-01T09:00:00Z"}}
```

```json
{"decision": "NO_ACTION",
 "future_item_actions": [
   {"item_ref": "fitem:<本轮 later_items 里给出的真实 ref>", "op": "reschedule", "in_minutes": 60}
 ]}
```

注意：`item_ref` 必须来自本轮 `situation_context.later_items`，不能自造；`in_minutes` 与
`due_at` 只能二选一；`future_item_actions` 每次最多 3 条（`life_future_items.py:614-627`）。
以上只按字段形状合成；与具体 Decision 类型组合是否被接受，仍以 AG-1 的 Decision 合法性为准。
已核 NO_ACTION 只要求 `selected_candidate_ref` 为空（`agency_decision_ag1.py:2333-2336`、
`:3251-3254`），没有禁止这两个可选键；`park_current` 的示意用 WAIT，符合提示的 park 规则。

---

## 问二：长期在做作品/项目能否自己推进？

**结论：能推进，但要分成两条路，且两条路证明的东西不同。已有作品是「续作/接续」，
新项目是「一次 owner 创建 + 第一版写作」；两者都不是自动计划，也不是长期目标系统。**

### 路 A：已有作品续作

- 候选由 `ArtifactContinuationSource` 提供（`artifact_continuation_source.py:85-125` 的
  `extra_opportunity_provider`；额外 ref 前缀 `fitem:` / `popt:` 在 `:289-299`；
  `refresh` 在 `:692`）。到期事项带 `artifact:` 来源时，会以 `FUTURE_ITEM_DUE` 进入
  `PERSONAL_OPPORTUNITY`，不新增候选种类（`autonomous_life_cycle.py:3771-3792`）。
- Decision 选中候选后，`_named_work_ref`（`:4571-4595`）按候选自己的 ref 解析目标：
  `fitem:` → 它命名的 artifact；`artifact:` → 原样；`CONTINUE_CURRENT` /
  `CURRENT_ACTIVITY` → owner 自己的 target refs。**不按「本轮碰巧绑定的那份」回落。**
- 如果 owner 已不认为该作品可续，adoption/step 都抛 `WORK_NOT_CONTINUABLE`
  （`:4603-4636`），不会静默换到另一件作品。这正是 rev214 之后保留的桥。
- 挂起中的同一作品不会因为到期事项再开第二个 Activity：正式链已改为要求 RESUME
  （`v05-due-parked-target-20261026.md`，修复只在来源的 extra 路径与 refresh）。

### 路 B：新项目选项（`popt:`）

`life_project_options.py:337-456` 的 `build_options` 本轮实际来源只有四类：

- **review**：B 的 practice workspace 已接线，且 A 的 Memory 本轮真的返回可读经历；
  标题与问题来自 `review_seed`，证据是经历 id（`:371-388`）。
- **questions**：她的私人文档里的 `unclear` 标记；标题模板是
  `研究：<她的问题>`（research 已接线，`:389-415`）或
  `把「<她的原话>」写成一个东西`（`:416-432`）；种子文本是她自己的原文。
- **due_items**：到期且带标题的事项，标题原样，种子是标题本身（`:434-454`）。
- **existing_projects**：**只用于标题去重**（`:363-366`），不生成「继续已有项目」选项；
  已有项目走上面的路 A。

选择一项后的真实写：

- 普通项目：`_project_adoption_spec`（`autonomous_life_cycle.py:3916-3952`）调
  `gateway.create_project(title, seed_text, source_refs, option_ref)`；owner 可用性不成立时
  具名 `PRIVATE_PROJECT_OWNER_UNAVAILABLE`，不建替代作品库。创建成功后读回 resume point，
  由 AG-2 以 adoption spec 起**新 Activity**；第一版由 `_project_step_spec`
  （`:4231-4244`）经 AR0/writer 写，不是模板复制。
- research 选项：走 research workspace 自己的 `create_research`（`:3954-3997`）。
- review 选项：走 review adoption（同文件分支）。
- 上限：`MAX_OPTIONS = 2`（`life_project_options.py:66`），且 `options[:limit]`
  （`:456`）按 review → questions → due_items 的顺序截尾（见问三缺口 1）。

### 题目与内容分别来自谁

- **题目**：模板 + 她自己的文本。research 前缀「研究：」、question 前缀「把「…」写成一个东西」、
  due 原标题、review 由 `review_seed` 生成。不是模型在本轮现编标题。
- **种子/问题文本**：她私人文档里的 unclear 原文、到期事项标题、或经历摘要；
  操作者没有直接「创建项目选项」的入口。操作者能间接影响的是她的阅读材料与私人文档内容。
- **作品正文**：由后续 AR0 step 和 writer 写，模型参与内容生成；不是模板。

### 三种情况必须分开

- 选 `popt:` = 一次真实 owner 创建 + 新 Activity（**START 路径**），不是 RESUME。
- 已有作品在同一 Activity 上继续 = **CONTINUE**，由 AG-2 按既有规则决定。
- 挂起后再回来 = 需要 LR-4 资格 + 显式 **RESUME**，不能靠 popt 或到期事项绕过。
  跨轮的 artifact 持久只证明「作品还在」，不证明「她在执行一个长期计划」。

---

## 问三：能力已存在但断在哪，还是压根没有？

**结论：能力入口存在，主链有正式真owner历史证据；本次发现3个需区分处理的静态疑点。** note/report的链本次未重跑；下面的路径分析不是严格运行反例，无法证明它们导致m未提出事项，也不能将有界排序自动判为缺陷。

### 缺口 1：无对象的到期项会被更早的项目选项挤出

- `build_options` 的追加顺序是 review → questions → due_items，末尾 `options[:limit]`
  （`life_project_options.py:371/389/434/456`），默认 `MAX_OPTIONS = 2`（`:66`）。
- 同时 `_extra_opportunities` 只把「带 `artifact:` 来源的到期项」放进候选
  （`autonomous_life_cycle.py:3771-3792`）；无对象的到期项**只能**作为项目选项出现。
- 所以当 review / question 已经占满 2 个名额时，无对象到期项既不是候选、也不是 popt，
  只剩 `later_items` / `due_item_refs` 里的名字。它仍可就地改期/取消，但没有「现在开始做」
  的可选入口。`v05-due-reconsideration-20261026.md:24-25` 验证的是单条到期项的场景，
  没有覆盖这个名额竞争。
- 性质：潜在饥饿，不是每次发生；是否要保底一个名额是产品取舍。

### 缺口 2：`park_current` 与 Decision 类型的一致性只有提示，没有代码强制

- `SYSTEM_PROMPT` 要求 `park_current=true` 时 decision 必须是 WAIT 或对应的显式条件 PAUSE
  （`agency_cognition_adapter.py:245-249`）。
- AG-1 的 `later_item` 验证只看字段形状（`agency_decision_ag1.py:3414-3441`；
  `life_future_items.py:671-712`），不看 decision 类型。
- 日循环在 Decision 提交后无条件写（`autonomous_life_cycle.py:4267-4295`），
  `parked=True` 只改变 origin 与 activity_ref。若非 WAIT/PAUSE 的 Decision 带了
  `park_current=true`，会存下一条 `ORIGIN_PARK` 条目，但前台不释放、没有 LR-4 条件。
- 性质：静态发现提示约束与所查字段验证不完全一致，但完整采纳分支是否另有拒绝未实跑，不能断言最终必落错误origin或“无任何效果”。若原规范已明确禁止且是校验漏接，可先用正式反例确认；若要新定合法组合或改变park含义，则是本人待裁定事项，不先一概归为新规则。

### 缺口 3：`later_item.note` 通过验证后被丢弃

- `validate_proposal` 接受 `note` 并放进返回结构（`life_future_items.py:681/699/706`）。
- 但 `_apply_future_item_writes` 调 `store.propose` 时没有传 `note`
  （`autonomous_life_cycle.py:4280-4291`），`FutureItem` 也没有 `note` 字段
  （`life_future_items.py:279-316`），`propose` 签名同样没有（`:435-438`）。
- 结果：模型写下的 ≤120 字备注永远读不回来，`later_items` 视图只有标题/到期/origin。
  这是字段级「接受但不持久」，不是核心链断。

### 已知边界（不是本次新发现，避免重复报）

- `times_considered` 只有 `note_considered` 会写，日循环不调用；它是报告字段，不是配额。
  `v05-due-reconsideration-20261026.md:44-45` 已明确记录。本次全仓 Grep 仍只见定义处
  （`life_future_items.py:518`），与既有记录一致，不另开缺口。
- 提示超预算时的 `later_items` 6→3→整块丢弃（`agency_cognition_adapter.py:899-908/945-951`）
  是有界裁剪，不是写入丢失；只在预算紧张时影响可见性。
- `m` 真实日没有观察到模型自然输出 `later_item` / 项目选择（`v05-fastday-m-20261026-result.md`），
  但这是单样本未出现，不能推成「有能力也不会用」或「能力无效」；历史原因记 UNKNOWN。

### 没有找到的

- 没有找到 operator 专用的 future item 创建 CLI；仓库内只有库类与测试/冒烟调用。
- 没有找到独立于模型提案的 `ORIGIN_MATERIAL` 生产者。
- 所查这条later-item/project路径未证完整长期目标、计划或承诺系统；不能由8文件扫描推全系统没有这类对象。作品持续和新项目创建已存在，但长期自主计划的自然采用仍未证。

---

## 如需产品决定：2—3 个最小方案

本单不新增规则，以下只供拍板。

**A. 保留现有规则，先验证名额竞争与最终请求（推荐的下一步，不是已执行）**
用新隔离正式反例区分review/question已占满名额、到期项仍在处境可见、能否当轮开工。保留NO_ACTION和原上限，不默认接`note_considered`（它会新增持久写，且当前只是报告字段）。如果需要到期优先或保留名额，再单独决定排序取舍；这会挤掉其它合法选项，是新产品规则，不能称无语义变化的漏接。

**B. 只修语义一致性**
在 AG-1 增加「`park_current=true` ⇒ decision ∈ {WAIT, 显式条件 PAUSE}」的校验，违规直接
`INVALID_SCHEMA`，不静默存条目。代价：改合法集，属产品可见行为；需要正负控并确认不误伤
合法的 park 路径。风险：模型某些原本能提交的答案会被拒。

**C. 改提示预算**
把 `later_items` 的 3 条裁剪后移，或把 `MAX_OPTIONS` 从 2 提高。代价最小，但直接改提示
预算/选项上限，属于产品规则调整；不建议作为第一步，且不应先靠加提示刺激来替代实现修复。

以上都未批准实施。不把三项静态疑点并成“解决没事做”的大改：优先保留既有能力，只有正式反例才能确认漏接。`later_item.note`若要持久保存，将涉及FutureItem数据形状/兼容与后续展示；若选择拒绝或移除该可选字段也会改变接受合同，需具名方案及批准，不能悄悄补新字段。

## UNKNOWN（本次未证）

- 未运行任何测试、实例、服务或真实模型；三问的自然采用率、真实日是否会输出这些键，均未证。
- 未证明本机当前装配的 project gateway / research / practice 三个 owner 都现场可用；
  只确认装配代码存在与历史报告中真 owner 链通过。
- 未逐行审计 AG-2 是否在 `selected_candidate → project` 之外还有第二道校验；本报告只覆盖
  cycle 的 adoption/step 接缝。
- 未核实仓库外是否有运维脚本手工写 `life_future_items.json`；本仓 Python 内未找到入口。
- 未验证超预算裁剪在真实提示长度下多久触发一次；只确认顺序与上限。

## 本次读取的文件（实现最多 8 个）

`agency_cognition_adapter.py`、`agency_decision_ag1.py`、`autonomous_life_cycle.py`、
`life_future_items.py`、`life_project_options.py`、`artifact_continuation_source.py`、
`life_integration_ag2.py`、`autonomous_life_assembly.py`。

证据文档：`2026-11-02-v04-autonomous-day-later-and-multi-project.md`、
`v05-due-reconsideration-20261026.md`、`v05-due-parked-target-20261026.md`、
`v05-fastday-m-20261026-result.md`。
