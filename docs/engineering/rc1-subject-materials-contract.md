# rc1 主体私人材料 —— C 侧机制契约（版本敏感处境 + 同一 Activity 阅读续接）

> 表述纪律：本层只证明**机制**。测试里的决策来自脚本化替身 `ScriptedCognition`，它不是模型；
> `understanding` 恒 `UNKNOWN`。本轮 0 次真实 provider 调用、0 网络抓取、0 支付、0 Memory 写入，
> 也没有把主体常驻跑过一天。

- 仓库：`D:\nyairo-rc1-body-backend`（分支 `work/rc1-phone-world-body`，HEAD `41e818f`，dirty 成果保留）
- C 写集（本轮唯一 Life 核心写者）：`autonomous_life_cycle.py`、`artifact_continuation_source.py`、
  `private_daily_items.py`、`autonomous_life_resident.py`（只改报告取值）、新纯读模块
  `subject_private_materials.py`，以及 `tests/test_subject_private_materials.py`、
  `tests/test_subject_reading_cycle.py`、本文与 `rc1-subject-materials-core-result.md`、独占 note
- 未触碰：`components/phone/life.py` 与 `scripts/phone.py`（P 面）、`components/life/scripts/one_day_life.py`
  CLI、`components/world/**`、`components/supply/**`、`web/**`、`life_artifact_port.py` 的写入语义、
  `components/life/scripts/phone_reading.py`（阅读/手机面）
- 结果与真实测试输出：`docs/engineering/rc1-subject-materials-core-result.md`

---

## 1. 不变量（先写死，后面的细节都是它们的展开）

1. **owner 是唯一真相**。处境全部来自 ManagedArtifact owner（经 `LifeArtifactPort`）的读；模块没有
   store、没有缓存文件、没有第二本账。`subject_private_materials` 自己零写入。
2. **页码是位置，不是理解**。`understanding` 恒 `UNKNOWN`（`pdi.UNDERSTANDING_UNKNOWN`）。
3. **不补数据、不猜分类**。缺绑定/撤权/空/越界/owner 不可达各自一个状态；无 work 绑定时 cycle 的
   fallback 只应用「这个实例自己声明过哪些物件不是作品」这一条排除，不改 `latest_resumable` 语义。
4. **合法停点五条同时成立**：① 记录的 `source_id` 就是这份材料；② 它指向的条目真实存在、是一条
   `ITEM_SOURCE_ACQUISITION`、且它自己的 `source_id` 也是这份材料（别家的取得不算）；③
   `source_revision` 等于 owner **当前** head 版本（entry 与 position 都要）；④ 该 acquisition 未被
   撤权（`available` 未被置否）；⑤ 这一条属于该 `source_id` 的 position 集。少任何一条都不是停点。
5. **前台是本次阅读才能 CONTINUE 读页**。会不会被当成阅读续接，由 Activity owner 自己的记录判定
   （kind + `ACTIVE` + refs 命中这份材料），不从「有前台」推。
6. **读尽不是错误，也不制造机会**。读尽后不再产生可执行个人阅读机会；同一 Activity 允许合法
   `NO_ACTION`，不写、不转作品、不重复读最后一页。
7. **prompt 只用既有 bounded 通道**。不新增候选 kind、不改 AG-1 enum、不加假 Memory prior；
   事实键 ASCII、<=48 字符、本模块最多 8 个、合并后 <=24。

---

## 2. 纯读模块 `components/life/scripts/subject_private_materials.py`

### 2.1 签名

```python
PAGE_MARKER = "%%PAGE%%"
UNDERSTANDING_UNKNOWN = pdi.UNDERSTANDING_UNKNOWN
MAX_FACT_KEYS = 24            # 与 production prompt 的 observed_facts 预算一致
MAX_FACT_KEY_CHARS = 48       # 与 agency_cognition_adapter 的键长上限一致
MAX_READING_FACT_KEYS = 8     # 本模块自己最多占几个键

read_reading_situation(port, *, reading_artifact_id=None,
                       private_items_artifact_id=None,
                       page_marker=PAGE_MARKER) -> dict
reading_fact_keys(situation, *, limit=MAX_READING_FACT_KEYS) -> dict
merge_decision_facts(world_facts, reading_facts, *, max_keys=MAX_FACT_KEYS) -> dict
```

`port` 只被调用三个既有读入口：`read_artifact()`（材料与私人事项文档各一次）以及
`pdi.PrivateDailyItems(...).load()/items()`。没有 `commit_*`、没有 `lookup_operation` 写、没有 connect。

### 2.2 状态（12 个，互不顶替）

| 状态 | 真实含义 | `available` | `next_page` |
| --- | --- | --- | --- |
| `AVAILABLE` | 当前 revision 上还有可读页 | `True` | 有值 |
| `EXHAUSTED` | 当前 revision 已读尽（正常结果） | `False` | `None` |
| `READING_NOT_BOUND` | 实例没有命名材料/私人事项文档（含只有一半绑定） | `False` | `None` |
| `MATERIAL_UNCOMMITTED` | owner 有该物件但没有已提交版本（或读时 `NOT_FOUND`） | `False` | `None` |
| `MATERIAL_NOT_ACTIVE` | 物件 lifecycle 不是 `ACTIVE` | `False` | `None` |
| `MATERIAL_EMPTY` | 内容为空/纯空白 | `False` | `None` |
| `POSITION_NOT_A_PAGE` | 最近有效停点不是页号（locator 字符串） | `False` | `None` |
| `POSITION_OUT_OF_RANGE` | 停点页号超出当前 revision 的页数 | `False` | `None` |
| `SOURCE_REVOKED` | 当前 revision 的所有 acquisition 都被标记不可用 | `False` | `None` |
| `PRIVATE_ITEMS_UNAVAILABLE` | 私人事项文档读不出/解不出 | `False` | `None` |
| `OWNER_UNAVAILABLE` | owner 连不上（`LifeArtifactUnavailable`） | `False` | `None` |
| `OWNER_REFUSED` | owner 应答但拒绝读（含权威拒绝） | `False` | `None` |

### 2.3 返回形状（固定，缺的事实是 `None` 而不是编造）

```python
{
  "reading_artifact_id": str | None,
  "private_items_artifact_id": str | None,
  "source_id": str,                      # "artifact:<id>"（只一层 artifact:）
  "state": str, "reason_code": str, "detail": str,
  "available": bool, "exhausted": bool, "understanding": "UNKNOWN",
  "material": {"available", "lifecycle", "version_id", "version_number",
               "pages", "content_chars", "title", "error"},
  "position": {"page", "next_page", "position_kind", "set_at", "position_id",
               "acquisition_id", "on_current_revision", "needs_reread",
               "needs_reread_reason", "prior_revision_pages"},
  "private_items": {"ok", "owner_version_number", "doc_revision", "error"},
  "annotations": {"note", "unclear", "stale_note", "stale_unclear"},
}
```

- `position` 取「有效停点」里 `(set_at, 写入顺序)` 最大的那一条；**不是** `max(page)`。
- **`needs_reread` 是对「这次真正被用的那条停点」的判断，不是对历史的判断**：
  - 当前 revision 上有有效停点 → 只由这条停点自己的记录决定（刚落的当前停点即 `False`，
    `needs_reread_reason=""`），旧版页不再让它永久过期；
  - 没有当前有效停点、但存在旧 revision 的页号记录 → `needs_reread=True` +
    `needs_reread_reason="SOURCE_REVISION_ADVANCED"`。
  - 代码里还留了一条「旧停点自身带 `needs_reread` 标记 → 用它自己的 reason」的分支，但
    `PrivateDailyItems.items()` 的元数据白名单并不暴露 `needs_reread` 字段，所以这条分支目前恒空；
    真实来源只有「旧 revision 的页号记录」。
- 旧 revision 的页号始终进 `prior_revision_pages`（升序）；旧版最高页不驱动新版。
  这是「处境」而不是页码，所以有当前停点时它也照报，只是不再把停点判成过期。
- `annotations` 只数条数（memo 需带指向本材料的 `reading_anchor`；unclear 按 `source_id`），
  `stale_*` 是锚点/标记落在旧 revision 的条数。**不返回任何笔记正文。**
- `pages` 由材料内容的 `%%PAGE%%` 切分得出；没有 marker 就是 1 页，绝不是 0 页。

### 2.4 `next_page` 决策顺序（读尽 vs 越界是两件事）

1. `SOURCE_REVOKED` → 无下一页；
2. 当前 revision 无有效停点 → `AVAILABLE`，`next_page=1`，`detail="NO_STOP_ON_CURRENT_REVISION"`；
3. 停点不是页号 → `POSITION_NOT_A_PAGE`；
4. `page > pages` → `POSITION_OUT_OF_RANGE`（报 `STOP_PAGE_x>PAGES_y`，不修、不夹）；
5. `page >= pages` → `EXHAUSTED`（`LAST_PAGE_x_OF_y`），`next_page=None`；
6. 其余 → `AVAILABLE`，`next_page=page+1`（`NEXT_PAGE_x_OF_y`）。

### 2.5 事实键（进 production prompt 的唯一途径）

`reading_fact_keys()` 只产出下面这些键（按优先级，超出 8 个或超 48 字符就丢掉，不截断成另一个事实）：

| 键 | 何时出现 |
| --- | --- |
| `reading_state_<STATE>` | 恒有 |
| `reading_page_<n>` | 有有效停点页号时 |
| `reading_next_page_<n>` | 有下一页时 |
| `reading_pages_<n>` | 材料页数已知时 |
| `reading_exhausted_true/false` | 恒有 |
| `reading_needs_reread_true/false` | 恒有 |
| `reading_notes_<n>` | 笔记数 > 0 |
| `reading_unclear_<n>` | 未懂标记数 > 0 |

`merge_decision_facts()` 先放阅读事实、再放 World 事实，累计到 24 个键为止；不能过
`agency_cognition_adapter` 投影（`isascii()` 且 `replace("_","").isalnum()`，<=48）的键直接丢弃。
合并丢掉的键由 cycle 写进回合结果的 `decision_facts_dropped`，不静默。

---

## 3. 版本敏感阅读路线 `components/life/scripts/autonomous_life_cycle.py`

新增/改动（行号为当前工作树）：

- `READING_ACTIVITY_KIND = "READ_PERSONAL_MATERIAL"`（L82）与 `private_materials_module()`（按需导入）。
- `private_material_situation(*, refresh=False)`（L1381）：本回合一次读（缓存
  `_reading_situation_cache`，`refresh=True` 重读），另存 `last_reading_situation` 供报告；
  未绑定三件套时返回 `None`。
- `private_material_facts(*, refresh=False)`（L1401）：上面处境的键投影。
- `reading_available()`（L1408）：现在等于「当前 revision 上还真的有一页」，而不再等于「绑定了」。
  完全没接处境读取器的裸 cycle 保持历史语义（绑定即可用）。
- `_reading_step_spec()`（L1476）：
  - 页号来自处境的 `position.next_page`（不是 `reading_position_page()+1`）；
  - 处境不可用 → `AutonomousLifeError("no page to read …")`，回合失败关闭、零写；
  - **规划中被换版**（处境里的 `material.version_id` != 刚读到的 head）→ 失败关闭，本轮不读不写；
  - `page > pages` → 失败关闭（不夹到最后一页）；
  - 结果新增只读键：`planned_from`（`NO_STOP_ON_CURRENT_REVISION` /
    `LATEST_STOP_ON_CURRENT_REVISION`）、`previous_page`、`reading_state`、`needs_reread`。
- `_foreground_reading_activity()`（L1614）：读 owner 的 `get_current_life_view()` /
  `get_activity()`，要求 kind 是 `READ_PERSONAL_MATERIAL`、`status == "ACTIVE"`、refs（`subject_refs` +
  `target_ref` + `checkpoint_ref`）里有这份材料的 artifact 身份；任一不满足返回 `None`
  （== 不当作阅读续接）。读失败写 `last_error`，不猜。
- 回合主流程：
  - `foreground_reading` 命中时把 `foreground_reading_activity_ref` 放进 base，并且**不建 AR-0
    work 步骤**（L2038 附近），所以阅读 CONTINUE 不会顺手提交作品版本；
  - 阅读步骤允许条件从 `not has_foreground` 改为 `not has_foreground or foreground_reading is not None`；
  - 决策之后：前台是本次阅读时，只有「决策确实选了这份材料且有可用阅读步骤」才走
    `_classify_reading_step`；否则一律 `NO_ACTION`，带 `reading_continued=False`、
    `reading_state=<状态>` 与具名 `detail`（「材料没有下一页了」/「决策没选这份材料，没有拿别的东西替它执行」）。
    这条短路就是「阅读 CONTINUE 被当成作品续写」的封堵点。
- 处境报告与候选：`_inventory`-风格收集处新增 `found["private_materials"]`（state/available/
  exhausted/detail/material/position/annotations/understanding，纯报告），**只在 `available` 时**
  才同时给 `found["reading_material"]`（既有 PERSONAL_OPPORTUNITY 材料项）。
- `observed_facts` 通道：回合里 `decision_facts = merge_decision_facts(world_facts, material_facts)`，
  base 里留 `private_material_facts`（排序后的键）与 `decision_facts_dropped`；
  交给 AG-1 的 `observed_facts=decision_facts`。没有新 kind、没有 Memory prior、没有正文。
- 每回合开始清 `_reading_situation_cache`（一轮一个瞬间：候选、步骤、事实不许在一个回合里互相矛盾）。
- 作品选择：`_latest_resumable_work()`（L1243）+ `resume_selection()`（L1275）。前者是 cycle 自己的
  fallback：只对 `ACTIVE` 物件按 revision 取最新，跳过 `artifact_exclusions`（阅读材料、私人事项文档），
  跳过的 id 报在 `latest_resumable_skipped_non_work`；`LifeArtifactPort.latest_resumable` 一字未改。

### 3.1 回合结果字符串（不是进程退出码）

阅读分支会出现的 `outcome`：`ACTED`（真读了一页并 CAS 落停点）、`NO_ACTION`（读尽/决策没选这份材料）、
`PAUSED_BY_OPERATOR`（暂停，连决策都不问）、`STEP_UNKNOWN`（UNKNOWN 只对原键对账）、
`STEP_REFUSED`、`ADOPTION_REJECTED`、`DECISION_FAILED`。这些是数据字段，不是命令行退出码；
C 侧没有 CLI 入口，进程退出码只出现在测试运行器上（见 core-result）。

---

## 4. 候选新鲜性 `components/life/scripts/artifact_continuation_source.py`

```python
ArtifactContinuationSource(port, *, per_source_cap=..., exclude_artifact_ids=None,
                           reading_artifact_id=None, reading_state_provider=None)
```

- 新增只读属性：`last_reading_state: dict[artifact_id, situation]`、
  `last_reading_skip_reason: str | None`。
- `_reading_state()`（L143）：provider 抛异常 → 记 `READING_STATE_READ_FAILED`（不可用，失败关闭）；
  返回非 mapping → `READING_STATE_NOT_A_MAPPING`。没接 provider 时源行为与改动前完全一致。
- `_read_points()`（L164）：只对**被显式命名的那一个** `reading_artifact_id` 施加处境过滤——
  处境 `available=False` 就不产出这条候选（读尽不再每轮制造可执行 START），并记 skip reason。
  没有把分类规则推给别的 artifact，也没有改「每个 ACTIVE 物件都可续」的通用规则。
- `_item_for()`（L218）：仅对阅读材料在 `detail` 里附上只读处境：`reading_state`、
  `reading_revision`、`reading_pages`、`reading_page`、`reading_next_page`、`reading_needs_reread`、
  `reading_exhausted`、`reading_notes`、`reading_unclear`。不加 kind、不改合法决策、不含正文。
- `refresh()`（L374）：材料分支先读当下处境——
  - `available=False` → `invalidate(..., reason_code="READING_STATE_<STATE>",
    new_status=CANDIDATE_STATUS_WITHDRAWN)`；
  - `_reading_progress_changed()`（L353，比较 `version_id` / `page` / `next_page` /
    `private_items.owner_version_number`）→ `READING_PROGRESS_ADVANCED` + `CANDIDATE_STATUS_STALE`；
  - 其余照旧 `OPEN`。

换句话说：材料换版、停点前进、私人事项文档长了一版，都会让旧候选过期，而不是被静默复用。

---

## 5. 私人事项窄 API `components/life/scripts/private_daily_items.py`

- 常量：`READING_ANCHOR_FIELDS = ("source_id","source_revision","page")`（L119）、
  `READING_OPERATIONS_FIELD = "reading_operations"`（L125）、`MAX_READING_OPERATIONS = 200`（L127）。
- memo 可选 `reading_anchor`（结构性元数据：哪一页，不是理解），进 `_METADATA_FIELDS[ITEM_MEMO]` 白名单；
  `revise_memo` 只换正文，不抹锚点；未知字段名 → `READING_ANCHOR_UNKNOWN_FIELD`（拒绝，不静默丢）。
- `reading_operation(document, operation_key)`（L393）：读 `reading_operations[<key>]` 的最小归属记录
  （动作/页/版本/条目 id），只用于原键重放与跨 kind 冲突判断，不含正文、不进 `metadata_view`。
- `_commit(..., record=None, expected_head=None)`（L968）：`record` 与效果写进**同一个版本**；
  `expected_head` 是调用方自己的 CAS 前置，本地先比一次、再交给 owner 比，陈旧 head →
  `PrivateItemsConflict("CAS_CONFLICT:stale expected_head:<key>")`。
- `add_memo(..., reading_anchor=None, record=None, expected_head=None)`（L1066）。
- `record_page_read(...)`（L1250）：一次读页 = 一个版本（acquisition + position 同时落，plan 复用
  `plan_page_read`），返回 `{"write": WriteResult, "acquisition_id":..., "position_id":...}`，
  半步成功不可表示。
- `mark_unclear(..., record=None, marker_id=None, expected_head=None)`（L1303）：`marker_id` 让
  「同一句话标在两页」保持两条记录；不传则维持历史 intent 派生 id。
- `reading_position_page()`（L375）保留原样（历史「最后一条停点页号」），cycle 不再用它算下一页。

> 归属说明：本文件这批窄 API 与 `life_artifact_port.operation_row()`（只读转发
> `lookup_operation`）是同一 dirty 树里阅读/手机面（`components/life/scripts/phone_reading.py`
> 调 `record_page_read` / `reading_operation` / `operation_row`）与 C 侧处境读共用的接口。
> C 侧只**读**它们（`PrivateDailyItems.load()/items()`），不靠它们写；`life_artifact_port.py`
> 的写入语义本轮未改。

---

## 6. resident 起跑基线 `components/life/scripts/autonomous_life_resident.py`

`baseline()`（L477 附近）不再用 `artifact_port.latest_resumable()` 决定「这轮要续的作品」，
改为调用 cycle 自己的 `resume_selection()`，并额外回报：

- `work`：cycle 真的会续的那个 `ArtifactResumePoint`（没有就是 `None`）；
- `work_selection`：cycle 的选择报告（含 `selected_by`、`bound_work_artifact_id`、
  `latest_resumable_skipped_non_work` 等）；
- `work_selection_source`：`CYCLE_SELECTION`（正常）或 `LATEST_RESUMABLE`（无 `resume_selection` 的
  兜底，实际不会发生）。

这样「报告里说是这份作品、回合里却续了另一份」以及「把 PDI 当作品」都不会发生；`latest_resumable`
的通用语义仍然原封不动留在 port 上。

---

## 7. 明确没做（边界）

- 没有新候选 kind、没有改 AG-1 enum、没有假 Memory `prior_experience`、没有第二套分类器。
- 没有把 `understanding` 从 `UNKNOWN` 变成任何分数；页码 ≠ 理解。
- 读尽后**不自动完成**阅读 Activity：允许合法 `NO_ACTION`/`PAUSE`/`ABANDON`，不新建总脑、不改
  Activity schema 去强制完结。
- 没有真实 provider 调用、没有把手机实例常驻起来；P 面入口（`life-preflight` / `life-run`）见
  `rc1-subject-materials-runbook.md` 与 `rc1-subject-materials-binding-result.md`，本轮未改。
- 测试驱动只有 fake cognition（`ScriptedCognition` 从 AG-1 真实给出的候选里按脚本挑），
  所以「她自己选了读书」不成立；能成立的是「候选真合法、路由真到 owner、停点真落盘」。
