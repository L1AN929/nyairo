# v0.5 首单：今天事项优先占位 —— 权威来源核查与具名 STOP

日期：2026-10-26。范围：只读核查「今天排定」「外部义务」的权威字段与生产入口，判断能否在不新增持久格式的前提下做分型优先占位。结论是**不能**，本单按任务书交具名 STOP 与可施工方案，未改业务源码、未跑测试/服务/模型。

## 父级复核限定（尚未批准施工）

推荐保留B的分域方向，但把首两单合成正式Decision排程→权威记录→候选占位的完整链，不以夹具手写文件冒正式入口。建议业务日+08:00、原2常规位外自选/义务各最多1位，总至多4；均仍过来源、可执行和恢复资格门，NO_ACTION合法。新增格式及排程协议待本人批准。

以下草案不是定案：义务必须绑定现成可执行对象及完成依据，不能把“上学”标题自动转为写作项目；首版不新增学校/打工动作。外部义务定义只由operator写，Life完成证据从既有owner结果另行投影；不能既称Life只读义务文档，又让其日终写MISSED。日终状态/顺延另议。缺文件可保旧行为，损坏/不可读必须具名区分。路线A不必永远沿v1，新读端兼容旧格式、旧读端拒新格式也是可选方案；数据分型不自动等于文件权限隔离。下文“全仓唯一/不存在”等穷尽说法仅作为worker静态报告保留，不升级为父级证明。

## 结论

本次所查FutureItems与Life接线路径中**未找到**「今天排定」权威字段或已接入的「外部义务」类型/生产入口；这是限定静态核查，不是全仓能力不存在的证明。「稍后事项」(`life_future_items.py:60-63`) 的三个 origin 全部是她自己或材料产生的，没有任何外部给定值；每条记录的 `due_at` 只是一个 UTC 时刻，没有计划日、没有业务日语义，也没有“她把它排进今天”这个动作。因此“义务和自选各一个不被 review/question 占掉的位”所需的最小前提——**两个可区分的权威类型**——不存在，任何实现都只能靠标题或 source_ref 猜类型，而任务书明令禁止。

按停止条件，本单不动候选接缝，不造临时字段，不写“先塞进内存排序”的半套实现。下面给出核查事实、两条真实替代路线与推荐，等本人批准新格式后再施工。

## 核查事实（静态读，未运行）

### 1. 「今天排定」没有权威字段

- `FutureItem` 的字段是 `item_ref/title/due_at/status/origin/created_at/updated_at/subject_id/activity_ref/workspace_id/source_refs/revision/times_considered/last_considered_at/resolution_reason_code/idempotency_key`（`components/life/scripts/life_future_items.py:280-315`）。没有计划日、没有“属于今天”、没有 day/window 字段。
- `due(now)` 只做 `due_at <= now` 的纯比较（`:428-431`、`:339-344`），`view()` 的 `due_refs` 同理（`:538-559`）。它们回答“时钟走到哪”，不回答“她哪天打算做”。
- 状态只有 `OPEN/CANCELLED/DONE`（`:50-52`），没有“今天没做成/未完成”这一态。规格里“没做就如实记录未完成”需要的词表也不存在。
- 唯一写口是 Decision → `_apply_future_item_writes` → `FutureItems.propose`（`autonomous_life_cycle.py:4246-4303`、`life_future_items.py:435-478`），写入的是“她自己的稍后”，没有“排到今天”的语义。

### 2. 「外部义务」没有类型，也没有生产者

- `ORIGIN_MODEL_PROPOSAL / ORIGIN_PARK / ORIGIN_MATERIAL`（`:60-63`）分别表示她的提案、搁置当前活动、材料里的问题；没有 operator/世界包给定值。`propose()` 对 origin 做白名单校验（`:447-449`），新值会被具名拒绝。
- 全仓检索 `obligation`/`义务` 在 Life 侧无命中；命中的是 World 的 `world_owner_funding.py`（投递义务，非日常安排）与 `world_school_assignments.py`（学校作业）。
- `world_school_assignments.py` **是**一个真实的外部事实源：带 `assignment_kind/subject/due_at/source_class/status`，`ASSIGNMENT_STATUSES={OPEN,DUE,CLOSED}`（`:30-70`）。但：
  - 它是 World 投影，开关 `CHIYO_WORLD_SCHOOL_ASSIGNMENTS_V1_ENABLED` 默认关（`:38`、`:130-131`）；
  - 每天最多 1 条（`MAX_ASSIGNMENTS_PER_DAY = 1`），且按学校日/时段稀疏生成；
  - **Life 侧零引用**（`components/life/`、`scripts/` 对 `school_assignment` 无命中），它不进 due 集合、不进候选、不进项目选项；只有 `world_living_skeleton.py:483-696` 读它。
  - 所以它不是“今天义务”的现成生产入口，接它等于新增一条跨域合同（World 事实 → Life 候选），不属于既有字段。
- 全仓只有一处 `FutureItems(...)` 实例化：`autonomous_life_assembly.py:927`。没有 operator CLI、没有手机写入口、没有第二个创建路径；`components/phone/` 对 `future_item/later_item` 零引用。

### 3. 业务日期语义在 Life 内不存在，跨域还不一致

- Life 的 `_now_iso()` 就是回合时钟（普通模式 UTC 墙钟，压缩演示是业务钟，`autonomous_life_cycle.py:5335-5341`），没有“今天”这个边界。
- 现有的“今天”只出现在别处：Activity/手机 `GET /api/phone/today` 用 `+08:00`（`components/phone/service.py:93-98`，注释明确对齐 `activity_compat.BJT = ZoneInfo("Asia/Shanghai")`）；World 用 `WORLD_TIMEZONE = "Asia/Tokyo"`（如 `world_school_day.py:23`）。
- 也就是说，“今天”在 Life/手机是 +08:00，在 World 是 +09:00。义务若从 World 来，日界要显式定一个，不能两域各算各的。

### 4. 名额竞争与预留接缝（供方案用，本单不改）

- 顺序固定 review → question → due，末尾 `options[:max(0, int(limit))]`（`life_project_options.py:371/389/434/456`），`MAX_OPTIONS = 2`（`:66`）。
- 项目选项同时进两处：提示上下文 `ctx["project_options"][:4]`（`autonomous_life_cycle.py:3572-3600`）与 `_extra_opportunities()` 生成的 `popt:` 候选（`:3771-3799`）。
- 处境指纹含选项 ref 列表（`:5135-5136`），集合变化才重新问；`ArtifactContinuationSource.refresh` 负责撤回本轮不再提供的 extra。
- 有界提示本身已经允许最多 4 条项目选项（`[:4]`），所以“2 常规 + 2 预留 = 4”不需要改提示预算，只改选项构造的截断规则。

## 为什么不能“用既有字段最小实现”

| 凑法 | 为什么不行 |
|---|---|
| 按 `source_refs` 前缀判断义务/自选 | 来源 ref 是“这条意图引用过什么”，不是类型权威；模型可自由命名，猜错就把她的自选当义务（或反之），且任务书明确禁止 |
| 把 `due_at` 落在今天的条目当“今天排定” | 直接把 due 等同计划，违反“不把所有 due 自动认作今日自选”；她也可能把同一件事排到明天或 14 天后 |
| 用现有 origin 推断 | 三个值都不表达外部给定；把 `MATERIAL_QUESTION` 读成义务是语义错 |
| 用 `title` 前缀（如「义务：」） | 标题是自由文本，不是权威；等于把规则藏进提示词 |
| 只给 due 保留一个名额，不做类型 | 任务书要求义务/自选各一个；一个无名额无法满足，也无法给手机分栏 |

## 两条替代路线

### 路线 A：在既有 `FutureItem` 上加类型与计划日

- 文档仍是 `life_future_items.json`，`schema_version` 保持 `chiyo.life.future_items.v1`；行内新增可选字段：
  - `class`: `SELF_PLANNED | OBLIGATION`（旧记录缺省 `SELF_PLANNED`）
  - `planned_for`: `YYYY-MM-DD`（业务日，旧记录缺省 `null`，表示“只按 due_at，不排某一天”）
  - `origin` 增一值 `EXTERNAL_GIVEN`；义务行由外部写入方给出
- 旧记录默认语义：缺 `class` = 自选，缺 `planned_for` = 无计划日 → 与今天完全同行为。
- 兼容性：`FutureItem.from_dict` 忽略未知键（`:319-331`），`_write_state` 原样写回 `items` 行（`:383-407`），所以新增行字段不会丢；但 `read_state` 对 `schema_version` 做**精确**匹配（`:377`），升 v2 会让旧读端直接 `FUTURE_ITEM_STATE_SCHEMA_MISMATCH`，因此这条路线**不能升版本**，只能在同一版本下加可选字段。
- 谁能写：自选由 Decision 写（新增排程动作，属单 2）；义务要新增一个写入口，但同一个文档出现两个写主体（她/外部），取消与改期权限必须在领域层按 `class` 分流，否则外部义务会被她的 `future_item_actions` 取消/改期。
- 取消/改期：自选沿用现有 `cancel/reschedule`；义务必须具名拒绝（如 `OBLIGATION_NOT_CANCELLABLE`）。“没做成”还需要新增状态 `MISSED`（现词表没有）。
- 日期与时钟：`planned_for` 由回合时钟 `_now_iso()` 换算到 `+08:00` 的日期。
- 代价：与 `life_future_items` 的既有合同冲突——该 note 明写一条 item “不是任务、承诺、日历或目标，也不是别人递给她的清单”（`life_future_items.py:1-20`，note `2026-11-02-v04-autonomous-day-later-and-multi-project.md:27-32`）。把外部义务塞进同一 store，等于改写这个域的边界，读端也容易把义务当“她自己的稍后”。

### 路线 B（推荐）：独立的日计划文档 + 外部义务只读文档

两件文档，各只有一个写主体：

- `life_day_plan.json`（Life 自有，`chiyo.life.day_plan.v1`），只由她的 Decision/收尾写：
  ```json
  {"schema_version": "chiyo.life.day_plan.v1", "revision": 3,
   "entries": {"plan:<hex>": {
     "plan_ref": "plan:<hex>", "date": "2026-10-26", "class": "SELF_PLANNED",
     "item_ref": "fitem:<hex>", "status": "PLANNED",
     "source": "SUBJECT_DECISION", "revision": 1,
     "created_at": "...", "updated_at": "..."}}}
  ```
  自选条目**只引用既有 fitem，不复制标题**（标题读 fitem，读不到就具名 `PLAN_ITEM_UNREADABLE`，不回落）。这样“她想做什么”仍只有一份权威。
- `life_day_obligations.json`（外部所有，`chiyo.life.day_obligations.v1`），Life **只读**：
  ```json
  {"schema_version": "chiyo.life.day_obligations.v1", "revision": 1,
   "entries": {"oblig:<hex>": {
     "obligation_ref": "oblig:<hex>", "date": "2026-10-26", "title": "上学",
     "due_at": "2026-10-26T08:30:00+08:00", "status": "PLANNED",
     "source": "OPERATOR", "source_ref": "schoolday:2026-10-26",
     "revision": 1, "updated_at": "..."}}}
  ```
  写入方是 operator（或以后由 World 适配器生成，见下），Life 不产生、不修改、不删除义务。
- 旧记录默认语义：两份文档都不存在 = “今天没有计划/义务”，预留位为空，输出与今天**逐 ref 相同**；不需要迁移任何旧记录。
- 谁能写：自选仅她的 Decision（单 2 加排程动作与写口）；义务仅 operator（单 1 只需只读投影，写口可以是既有的运维导入，不在 Life 内）。
- 日期与时钟：`date` 用 `+08:00` 业务日（与 Activity/手机 today 同一日界），由回合时钟换算；`due_at` 存绝对时刻。若以后接 World（Asia/Tokyo），只转换时刻、不搬 World 的本地日期，避免两个日界打架；这条转换要单独立项，不在单 1。
- 取消/改期权限：自选沿用现有 `future_item_actions`，取消/改期后 plan 条目随之 `ABANDONED` 或移出当天；义务在 Life 侧无 cancel/reschedule 写入路径，读投影只暴露 `PLANNED/DONE/MISSED`，任何写入都必须由外部文档变更（Life 只读），因此“她不能取消义务”是结构性保证，不是提示词约束。
- 状态词表：`PLANNED | DONE | MISSED | ABANDONED`（只读投影先用三个终态；`MISSED` 的写入在单 3 收尾，单 1 只读）。
- 代价：新增两份文档与一个只读投影；`life_day_plan.json` 的“她排今天的动作”要靠单 2 才能产生真实条目，单 1 单独无法自然长出计划（见下面的验收依赖）。

### 两条路线共用的候选接线（预留不改变其余排序）

1. `AutonomousLifeCycle.project_options()` 每轮从两份文档取“今天且仍可开工”的条目，得到至多两个预留项（义务 1、自选 1，各自按 `due_at, ref` 取最早一条，确定性），作为 `build_options(..., reserved=...)` 的新参数传入。
2. `build_options` 先按现行规则构造 review → question → due 并截断到 `limit`（**顺序与内容一字不改**），再把未出现在常规结果里的预留项追加到末尾；有效上限 `limit + 2`，没有计划/义务时仍是 `limit`。
3. 因 `ctx["project_options"][:4]` 本就允许 4 条，提示预算不变；`_extra_opportunities()` 遍历的是 `_round_project_options`，预留项自动获得 `popt:` 候选并进入最终请求。
4. `refresh` 每轮按文档重新计算：取消/改期/过期/日期不符 → 本轮不再提供，既有 refresh 机制撤回；文档不可读 → 具名错误、预留为空、回合照常（不把“读不到”当“没有安排”）。
5. 不削弱既有门：预留项若指向已 PAUSED/WAITING 的作品，仍走既有 `PAUSED_TARGET_REQUIRES_RESUME` 过滤，不因“预留”而放行；无对象条目只作项目选项（不建无事可做的 Activity），与现行 due→popt 规则一致。
6. 不自动写完成：选择预留项只走既有 adoption/STEP；事项/计划状态不因“被考虑”变化。

### 与后续两单的依赖

- **单 2（排今天的动作）**：需要 Decision 提案新增一个“把某件待办排到今天”的合法动作，并写 `life_day_plan.json`。这是提案 schema 变化，必须单独批准；本单不能顺带做。
- **单 3（手机今天页）**：读 plan + obligations + 既有 today 视图，按 `+08:00` 分栏；顺延/放弃是写路径，另验幂等。单 1 不改 phone。
- **验收依赖（必须说清）**：任务书的 B 要求“从既有真实写入口形成权威今日自选/义务，不手塞最终 options”。在路线 B 下，自选条目的真实写口属于单 2；若只批准格式、不批准最小写口，单 1 的 B 只能靠夹具直接写文档，等于“手塞权威”，与任务书冲突。因此批准格式时应当同时批准**最小的文档写入口**（自选：cycle 内部的 plan 写函数；义务：operator 导入函数/文件约定），并把单 2 的 Decision 动作留在下一单。这一点请在批准时明确。

## 推荐

**路线 B**，理由：

1. 不碰 `life_future_items` 的既有合同（“不是别人递给她的清单”），也不需要在一个文档里让两个写主体分权；
2. 文档缺失 = 旧行为原样，零迁移、零版本升级风险（路线 A 若升版本会让旧读端直接拒绝文件）；
3. 义务“她不能取消/改期”成为结构事实（Life 只读），而不是领域里再加一批按 class 分流的拒绝分支；
4. 日计划天然是“按天”的对象，`date` + 只读引用 fitem，避免把 14 天范围的稍后事项硬压成日历；
5. 手机单 3 读一份文档即可分栏，不需要从 due 反推类型。

路线 A 不是不可做，它省一份文档、复用幂等键；但它要改一个已定案的域边界、在同一文档引入双写主体，并把“外部义务”伪装成“她的稍后”，代价比多一个文档高。

## 批准后施工的完整写集与离线验收

写集（路线 B，单 1 + 最小写口）：

- 新增 `components/life/scripts/life_day_plan.py`：两份文档的只读/单写主体领域（自选写、义务只读、日界换算、状态词表、读失败具名）。
- `components/life/scripts/life_project_options.py`：`build_options(reserved=...)`，只在末尾追加，不改常规顺序/截断。
- `components/life/scripts/autonomous_life_cycle.py`：每轮计算预留项并传入；报告里给 `reserved_refs` 与具名空位原因。
- `components/life/scripts/artifact_continuation_source.py`：确认预留 `popt:` 参与同一轮 extra 与 refresh 撤回，不碰既有 `PAUSED_TARGET_REQUIRES_RESUME`。
- `components/life/scripts/autonomous_life_assembly.py`：装配两份文档（读失败不影响既有装配）。
- 新增 `tests/test_v05_today_priority.py`、`scripts/v05_today_priority_smoke.py`、说明文档。
- 单 2 的 `agency_decision_ag1.py` / `life_future_items.py` 提案改动不在本批。

离线验收（批准后照任务书两条，父级独立复跑）：

- A：`build_options` 在 review/question 占满 `limit=2` 时，两个预留项仍追加且常规两项顺序不变；无文档时输出与今天逐 ref 相同；同一类两条时只取最早一条；取消/过期/改期出当天 → 无预留；文档不可读 → 具名错误且回合继续；预留指向已挂起作品 → 不给（既有门）；总额不超过 `limit+2`。
- B：真实隔离根（真 Supply owner、真 Memory、正式 cycle/runner、AG0/AG1 原始 JSON 脚本 Decision、AG2、AR0），经**真实写口**生成一条自选计划与一条义务，review+question 占满两席后，断言两个 `popt:` 出现在 `_round_project_options` 与最终请求候选集里；NO_ACTION 不写完成、不改计划状态；重复窗口不重复产出；负控覆盖挂起同目标、不可读、取消；服务正规关闭/排空/socket guard。

## 本单没做什么

- A/B 两条命令**NOT_RUN**：本单是格式 STOP，没有可实施代码，按任务书不为方案跑测试或服务。
- 未改任何业务源码、未写 test/smoke、未建实例、未调用模型、未访问旧 root。
- 未修改既有 implemented note：方案未批准，不在 implemented 状态里留提案（符合 `write-notes` 规范）。
- 产品调用 0，共同预算沿用 94/300、余 206；未 stage/commit/push。

## 引用

- `docs/engineering/v05-plan-own-day-spec-20261026.md`（范围与三单拆分）
- `docs/engineering/v05-due-option-competition-20261026.md`（甲反例：无对象 due 被截，不改排序）
- `docs/engineering/v05-self-authored-future-audit-20261026.md`（later_item 写口、popt 四类来源、名额缺口）
- `.agents/notes/implemented/architecture/2026-11-02-v04-autonomous-day-later-and-multi-project.md`（稍后事项域的边界）
- `.agents/notes/implemented/feature/2026-10-27-v04-private-projects-owner-and-phone.md`（项目选项与 owner 写权）
