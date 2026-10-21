# Agent Note: v0.4 等待恢复 —— 让"等一个真实条件"成为可选项，并接上 W 的收尾计划

Status: implemented

## Problem

0.4 缺的两件事其实是同一件："她不会等、也不会收尾"。

1. **WAIT 永远不在合法集。** AG-1 只在 `context_snapshot.known_waiting_basis_ref` 非空时才给
   `WAIT`（`agency_decision_ag1.py:2316-2321`），AG-2 只在拿到具体 `waiting_spec` 时才采纳
   （`life_integration_ag2.py:3321-3380`）。两边都实现了，**没有任何调用者喂养它**：cycle 只传
   capacity / observed_facts / prior_experiences（`autonomous_life_cycle.py:3533`）。于是"作品这一版
   写完了、材料读尽了"只能退化成 NO_ACTION / DEFER，活动继续占着前台，一天变成"一直做到没步骤"。
2. **到点在运行中不会发生。** LR-4 的 `scan_due_agenda` 只有 `recover_cold_start` 一个调用者
   （`activity_waiting_agenda.py:3189`），且它不在 `ag2_waiting_port` 白名单里
   （`life_runtime_production.py:550-553`）。不重启就永远等不到。
3. **已收尾的作品被反复当候选。** `_read_resume_point` 的"已绑定/历史指名"主路径不校验
   `point.resumable`，而兜底路径校验了（`autonomous_life_cycle.py:1451` vs `:1523-1547`）；作品一旦
   非 ACTIVE，`artifact_available` 仍为真，每轮为一个已关闭的作品构造 WORK 步骤，owner 以
   "artifact is not active" 拒一次。

## Decision

**1. 只允许两种真实依赖成为 WAIT 基础**（新模块 `life_wait_basis.py`，纯函数）：

| source | 判据 | baseline |
|---|---|---|
| `WORK_HEAD_AFTER_NO_CHANGE` | readiness `False` + `WORK_NO_CHANGE_CONFIRMED` | owner 当前 head（`_readiness_revision` 真读 owner） |
| `READING_MATERIAL_NEXT_VERSION` | readiness `False` + `READING_EXHAUSTED` | 材料当前 revision（owner 读） |

可执行步骤仍在、World 步骤未接线、目标命名空间未知、`SOURCE_REVOKED` / `MATERIAL_NOT_ACTIVE` /
`MATERIAL_EMPTY` / `PRIVATE_ITEMS_UNAVAILABLE` / `OWNER_UNAVAILABLE` / `OWNER_REFUSED` / `UNKNOWN`、
以及**没有 owner revision 的基线**，一律不给基础。没有"以后也许会变"这一档，也没有 timer / 自定 deadline。

**2. waiting 引用按 LR-2 的真实词表拼。** 侦察稿建议的 `artifact:<hex>` 会被 LR-2 直接拒
（`waiting_ref_vocab.VALID_WAITING_REF_PREFIXES` 里没有 `artifact`），所以条件与事件统一拼成
`wait_dep:artifact.<id16>.<baseline16>`：依赖族里合法，而且 baseline 进 ref —— 换了 head 就是另一个
依赖，旧 ref 不可能满足新条件。同一字符串同时给 LR-4 条件与事件（LR-4 按完全相等匹配，
`activity_waiting_agenda.py:1679-1686`）。

**3. SATISFIED 仍由 LR-4 判。** `head_change_event` 只在 owner 当前 head 真的不同于基线时产出
`EXTERNAL_RESULT_EVENT`；相同 head、无 head、owner 拒绝读一律 `None`（条件只能保持 PENDING）。
C 不 resume、不复放 Decision、不清 PAUSED 前台，也不清指针。

**4. upkeep 挂在既有 tick 上，不另起定时器。** `AutonomousLifeCycle.maintain_waiting` 做两件零模型的事：
调 `scan_due_agenda`（**唯一**的 port 窄增），以及把本模块绑过的 PENDING 条件按 owner 当前 head 重读；
只有真的变了才把**一个**事件交给 LR-4。**顺序是 `assembly.runner_tick` 里「先维护（一次）→ 再 poll feeds
→ 再 due」**：维护因此不依赖"有没有事件/窗口开没开"，NOT_DUE 也会扫到期、重读依赖（不花 request/round），
同一 tick 新开的 OPEN 资格当拍的资格 feed 就能看见。`wake`/`run_due`/`wake_on_event` 都带显式
`maintain=`/`maintained=` 参数 seam：tick 先跑一次并把报告传下去，所以同一 tick 只维护一次；`run_due` 在
due gate **之前**维护，`run_once`/控制 CLI 因此也覆盖 NOT_DUE。真正到期只产生 OPEN eligibility，"回不回来"
仍是下一个 Decision。

**5. 修 `_read_resume_point` 主路径。** 非 `resumable` 的绑定/历史作品返回
`(None, selected_by="NOT_RESUMABLE")`，**不改指到别的作品**（那是一个没人做过的决定），于是不再每轮产一个
必被拒的步骤。

**6. 收尾只做候选与路由，计划仍是 W 的。** `artifact_closeout.py` 惰性导入、四个函数齐备才接受；
候选资格 = 明确绑定的 work + 前台空闲 + owner 报 ACTIVE 且有 revision；目标 `closeout:artifact:<owner id
原样>`（真实形态 `closeout:artifact:artifact:<uuid>`，**不剥不复加**）。收尾与续写同源同对象，但候选
id / semantic_identity / idempotency_key 各自独立，续写候选身份逐字节未变。`_RoutedAdoptionSpec` 与
`_routed_step_builder` 各自多一条 closeout 分支，只在 committed Decision 自己的候选 `target_refs` 带
`closeout:artifact:` 时才走它；`START → AR-0(FILE/archive_artifact) → AR-1 settlement → LR-5` 与既有
`ACTION_RESULT_MATCH` 判据完全复用，**没有新增 decision 枚举、没有新增 criteria 种类**。计划不在时
`closeout_state()` 报 `NOT_AVAILABLE / ARTIFACT_CLOSEOUT_NOT_DELIVERED`，不产候选。

## Alternatives considered

- **`artifact:<hex>` 当 `waiting_on_ref`（侦察稿原案）**：最直观，"等的是那份作品"。否决原因很硬：
  LR-2 的 `wait_activity` 会按 waiting 词表校验这个 ref，`artifact` 不在里面 —— 采纳即被 owner 拒，
  等于把"WAIT 不可达"换成"WAIT 必被拒"。改成 `wait_dep:` 后同一个依赖仍然一眼可读，并且合法。
- **给所有"当前没步骤"的活动都发一个 WAIT 基础**：实现最省事。否决：World 步骤未接线、UNKNOWN 容量、
  预算花光都不是"某个 owner 欠我下一次结果"，把它们变成等待就是自造未来（16:68 的弱动机/无 deadline）。
- **在 wait 条件下写 `condition_expires_at` 或排一个 agenda item**：能让"等太久"看起来有出路。否决：
  那正是"timer 冒充自主"，也没有任何 owner 能证明这个 deadline。`waiting_spec` 里两者都不出现。
- **PAUSED 也发 WAIT 基础，顺便把前台让出来**：能让另一个活动上位。否决：PAUSED 的回来之路是 LR-4
  eligibility + RESUME，给它发新 WAIT 等于绕过那条资格；`release_foreground=True` 更是把"停下的事"变成
  "空位"。前台默认不释放。
- **非 resumable 时自动改指到最新的可继续作品**：省一次中断。否决：那是一个没人做过的决定，而且会让
  "过去的字符串重新签发当前目标"。只具名报告。
- **给 closeout 新增 decision kind / criteria kind**：语义最"干净"。否决：AG-1/LR-5 的词表是承重墙，
  只需要既有 `ACTION_RESULT_MATCH` + `FILE_ACTION`；新增枚举会把一次收尾变成新决策类型。
- **在 cycle 里自己实现收尾计划（target/spec/step）**：C 一个人能写完。否决：那是 W 的 owner 侧合同，
  两边各写一份就会漂移；C 只消费 W 的四个函数。

## Consequences

- 收益：WAIT 从"不可达"变成"有两种具名来源的可选项"，且仍然只花一次决策请求；到期维护在运行中发生、
  零模型、只产出 eligibility；已关闭的作品不再每轮被拒；收尾有了真实的候选与路由（真实归档由 W 与父级
  有限真链证明）。
- 代价 / 限制：
  - **gen1 真链抓到的实现缺陷（已修）**：closeout 路由谓词最初写成 `@staticmethod` 并直接调
    `AutonomousLifeCycle._selected_candidate(record)`，而那是实例方法 —— 真链里 AG-2 用 committed
    Decision 调路由时抛 `TypeError: missing 1 required positional argument: 'decision_record'`，整轮 WORK
    在 `step_path=AR0_READING_CLOSEOUT_ROUTED` 处 `DECISION_FAILED`。改为实例方法、经
    `self._selected_candidate` 读候选，并加了一条只在「旧写法会抛错」这条路径上才会失败的回归
    （`test_the_routed_adoption_spec_resolves_the_closeout_through_the_bound_predicate`）。
  - reconsideration 抑制仍在（`life_step_readiness.py`，非本写集）：某个无步状态若已被拒过一次，同一
    fingerprint 在 TTL（6h）内不会再被问，也就不会提供 WAIT。首次出现 no_change 的那一轮 `step_reason`
    不同，因此 WAIT 可达。
  - WAIT 被采纳时 wake 状态仍是 `PAUSED`（`_report_chosen_exit` 既有语义），决策字段是 `WAIT`；
    `runner.OUTCOME_CLASS` 未改。
  - 本包**没有证明真实一天**：没有真实模型 tick，"模型明确 WAIT → 真实新 head → 真 RESUME"与
    "真实归档 → settlement → LR-5"都要父级在停写后用原实例有限跑。
