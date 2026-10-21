# Agent Note: rc1 主体私人材料按「当前版本的真实处境」续读，阅读 CONTINUE 不再写成作品

Status: implemented

## Problem

rc1 之前，Core 的阅读路线只有一条粗线：`pdi.reading_position_page(document, source_id) + 1` 当作
下一页，`reading_available()` 只判断「材料 + 私人事项文档都绑定了」，候选源把阅读材料当普通
`PERSONAL_OPPORTUNITY` 每轮照发。于是出现五个具体错误，而且都在真实 owner 上成立：

1. **换版后读错位置**：旧版读到第 3 页，材料换成 2 页的新版后，算式仍给 `3+1=4`，直接越过新版最后一页
   （以前只报一句 `no unread page left`），新版第 1 页永远没人读。
2. **回前页被抹掉**：停点写的是「最近一次显式停点」，但阅读规划用 `max(page)+1` 的语义，回第 1 页之后
   下一轮会跳回第 2 页之上；显式停点不是停点。
3. **读尽仍制造机会**：读尽后材料依旧是 PERSONAL_OPPORTUNITY，AG-1 每轮都能「CONSIDER_PERSONAL_OPPORTUNITY →
   START」，形成空转的假机会。
4. **阅读 CONTINUE 变成作品续写**：阅读步骤的规划条件是 `not has_foreground`；同一 Activity 的
   CONTINUE 发生时前台非空，阅读步骤不被规划，决策落到作品分支，**一次阅读 CONTINUE 会提交一版作品**。
5. **报告把私人文档当作品**：`autonomous_life_resident.baseline()` 用
   `LifeArtifactPort.latest_resumable()`（通用「最近修改的 ACTIVE 物件」）报告「这轮要续的作品」，
   阅读材料或私人事项 JSON 一更新就可能被报成作品，而回合实际续的是另一个（或没有）。

另外页码信息此前只活在 ref（`artifactversion:…`）与候选 detail 里，进 `observed_facts` 时被
`agency_cognition_adapter` 的键投影（ASCII、`replace("_","").isalnum()`、<=48、最多 24 个）吞掉，
决策时看不到「我还有第几页」。

## Decision

新增**纯读**模块 `components/life/scripts/subject_private_materials.py`，把「我的材料现在到底什么处境」
做成一次 owner 读：

- `read_reading_situation(port, *, reading_artifact_id, private_items_artifact_id)`
  返回固定形状 `{state, reason_code, detail, available, exhausted, understanding, material,
  position, private_items, annotations}`；12 个状态互不顶替（`AVAILABLE`/`EXHAUSTED`/
  `READING_NOT_BOUND`/`MATERIAL_UNCOMMITTED`/`MATERIAL_NOT_ACTIVE`/`MATERIAL_EMPTY`/
  `POSITION_NOT_A_PAGE`/`POSITION_OUT_OF_RANGE`/`SOURCE_REVOKED`/`PRIVATE_ITEMS_UNAVAILABLE`/
  `OWNER_UNAVAILABLE`/`OWNER_REFUSED`）。
- **合法停点五条同时成立**：`source_id` 命中材料、它指向的条目真实存在且是
  `ITEM_SOURCE_ACQUISITION` 且其 `source_id` 也是这份材料、entry 与 position 的 `source_revision`
  都等于 owner **当前** head、该 acquisition 未被撤权。取 `(set_at, 写入顺序)` 最大的那条，
  **不是 `max(page)`**。
- **`needs_reread` 只对「这次真正用的那条停点」成立**（父级真实处境复核后定点修）：有当前有效停点
  时由这条停点自己的记录决定（刚落的当前停点即 `False`、reason 为空）—— 旧版页存在也不再让它永久
  过期；只有**没有**当前有效停点、且存在旧 revision 页号记录时才 `True` +
  `SOURCE_REVISION_ADVANCED`。旧版页号仍进 `prior_revision_pages`、旧版锚点笔记仍算 `stale_*`：
  这些是处境，不是「停点过期」。
- `next_page` 决策：无当前版本停点 → 1；停点非页号 → `POSITION_NOT_A_PAGE`；`page > pages` →
  `POSITION_OUT_OF_RANGE`；`page >= pages` → `EXHAUSTED`（没有下一页，不重复读最后一页）；
  其余 `page+1`。`understanding` 恒 `UNKNOWN`。
- `reading_fact_keys(situation)` 把数字写进**键本身**（`reading_page_2`、`reading_next_page_3`、
  `reading_pages_3`、`reading_state_AVAILABLE`、`reading_exhausted_false`、`reading_needs_reread_true`、
  `reading_notes_n`、`reading_unclear_n`），本模块最多 8 个、每个 <=48 字符；`merge_decision_facts()`
  先放阅读事实再放 World 事实、封顶 24 键，不能过投影的键直接丢弃并报进 `decision_facts_dropped`。

接到既有 runtime 上（全部在 C 写集内，不新增 kind、不改 AG-1 enum、不加 Memory prior）：

- `autonomous_life_cycle`：`private_material_situation()/private_material_facts()`（一轮一读、可 refresh）、
  `reading_available()` 改成「当前 revision 上真有一页」、`_reading_step_spec()` 从处境的 `next_page`
  取页并在「处境不可用 / 规划中被换版 / 页号越界」时失败关闭（零写）、结果只读回报
  `planned_from` / `previous_page` / `reading_state` / `needs_reread`。
- **前台阅读识别**：`_foreground_reading_activity()` 读 Activity owner 自己（kind=`READ_PERSONAL_MATERIAL`、
  `ACTIVE`、refs 命中这份材料），命中时①不建 AR-0 作品步骤②允许规划阅读步骤③决策后若没选这份材料
  就 `NO_ACTION`（`reading_continued=False`），绝不掉到作品提交。
- `artifact_continuation_source`：新增 `reading_artifact_id` + `reading_state_provider`，只对**被显式命名
  的那一个**材料按处境过滤（读尽/撤权不出候选），`refresh()` 对不可用发 `WITHDRAWN`、对进度前进
  （revision/停点/文档版本号）发 `READING_PROGRESS_ADVANCED` + `STALE`；候选 detail 附只读处境。
  通用「每个 ACTIVE 物件都可续」的规则与 `latest_resumable` 一字未改。
- `autonomous_life_resident.baseline()`：作品基线改取 cycle 自己的 `resume_selection()`
  （bound work → 历史选择 → 最近 ACTIVE 且**不是**本实例声明的非作品物件），并回报
  `work_selection` / `work_selection_source`；`cycle._latest_resumable_work()` 只应用
  `artifact_exclusions` 这一条排除，跳过谁就报谁（`latest_resumable_skipped_non_work`）。

## Alternatives considered

- **直接把版本敏感逻辑塞进 `LifeArtifactPort.latest_resumable` / `resume_point`** —— 最强理由是
  「只有一处真相、所有调用方自动受益」。否决原因：port 是通用端口，它不知道哪个 artifact 是「作品」、
  哪个是「私人事项文档」；把阅读/作品的分类写进 port 等于让通用层猜 kind，正是施工单禁止的事。
  现在保留 port 语义，把它收窄成 cycle 自己的 fallback 并显式报告跳过项。
- **新增候选 kind（如 `CONSIDER_PRIVATE_READING`）或改 AG-1 enum，让「下一页」成为一等候选** ——
  最强理由是「决策面更诚实：模型看到的就是可用动作」。否决原因：会改 AG-1 的 enum 与所有既有
  prompt/契约，超出本轮「复用现有 AG0/AG1/AG2 通道」的边界；而且事实键已经能把「有第几页」讲清楚。
- **继续用「前台为空才允许规划阅读」的旧条件** —— 最强理由是「AG-2 单前台规则简单，不会抢作品」。
  否决原因：同一 Activity 的 CONTINUE 天生前台非空，旧条件让阅读 CONTINUE 落到作品分支并提交作品
  版本（Problem 第 4 条），这是必须封堵的真实缺陷。现在改为「前台就是这份材料的页读」才放行。
- **读尽后自动 complete 阅读 Activity** —— 最强理由是「不留悬挂 Activity，状态干净」。否决原因：
  施工单要求「如不自动完成，报告等待/可暂停」，而自动完成需要新的完成合同判定（读尽≠理解完成），
  容易把「读完了」写成「读懂了」。现在停在合法 `NO_ACTION`/`PAUSE`/`ABANDON`，缺口写进交付结果。
- **保留 PDI 的 `reading_position_page()+1` 算式，只在后面加版本校验** —— 最强理由是「改动最小、
  历史行为最接近」。否决原因：它连「回前页是停点」都表达不了（历史 API 只返回最后一条停点页号，
  没有 acquisition/撤权校验），加校验等于把处境读重写一遍，且两套算式必然漂移。

## Consequences

- **收益**：换版从新版第 1 页重新认领（旧版最高页不再驱动新版）；回前页是真停点；读尽不再每轮制造
  可执行机会、也不重复读最后一页；阅读 CONTINUE 只能读下一页，作品 head 跨阅读轮不变；resident 报告
  与回合选择一致；处境以 ASCII 数字键进入 production prompt，正文/笔记正文不外泄。
- **代价与已知上限**：① 只在 fake cognition（`ScriptedCognition`）下验证，真实模型仍未被调用；
  ② 两个新测试文件是 `@posix_only`，原生 Windows 跳过，真实 owner 证据只在 WSL；③ 读尽后 Activity
  仍 `ACTIVE`（等合法 `NO_ACTION`/`PAUSE`/`ABANDON`），没有自动完成；④ 处境每回合多一次 owner 读
  （材料 + 私人事项各一次 `read_artifact`），换来一轮内候选/步骤/事实不互相矛盾；⑤
  `private_daily_items.py` 的窄 API（`record_page_read` / `reading_operation` / `expected_head` /
  `marker_id`）与 `life_artifact_port.operation_row()` 是同树阅读/手机面与 C 侧处境读共用的接口，
  C 侧只读；若将来阅读面改这些签名，C 的处境读要一起复核。

## Verification

- 只跑**新增类的 exact IDs**（`$env:PI_SCRATCH_DIR\c_run_new_exact.py`，从类 `__dict__` 取 `test_*`，
  不用 `discover`、不用类继承），WSL + `PYTHONDONTWRITEBYTECODE=1`：
  - `tests/test_subject_private_materials.py::ReadingSituationTest` → 11/11 `OK`，退出码 `0`
    （`scratch/c-new-materials.log`）；
  - `tests/test_subject_reading_cycle.py::SubjectReadingCycleTest` → 9/9 `OK`，退出码 `0`
    （`scratch/c-new-cycle.log`）。
  两段日志 `SUMMARY ids=… failures=0`，无 `FAILED`/`ERROR`/traceback。
- 定点修复（`needs_reread` 语义 + acquisition 的 source 校验）之后，只跑 4 个 exact ID
  （`$env:PI_SCRATCH_DIR\c_run_exact_ids.py`，断言方法确实定义在类自身 `__dict__`），
  `SUMMARY ids=4 failures=0`、退出码 `0`、日志 `scratch/c-fix-needs-reread.log`：
  - 新增 `…ReadingSituationTest.test_a_current_stop_is_not_made_stale_by_the_previous_revisions_pages` → `PASS`
    （当前版真实停点时 `needs_reread=False`、旧版页仍报 `prior_revision_pages=[1,2,3]`、`stale_note=1`）；
  - 新增 `…ReadingSituationTest.test_a_stop_that_points_at_another_sources_acquisition_is_not_a_stop` → `PASS`
    （position 指向别家 acquisition 时不算停点：`page=None`、`next_page=1`、`NO_STOP_ON_CURRENT_REVISION`）；
  - 受影响旧纯读用例 `…test_an_old_revision_stop_is_a_situation_not_a_page` → `PASS`（断言未改，仍 `True`）；
  - 受影响旧纯读用例 `…test_a_finished_material_is_exhausted_not_an_error` → `PASS`。
  未重跑 20 个全组、未重跑任何旧套件、未碰真实实例/owner 数据。
- 前一轮 C 的扩大运行已核对并停止：`scratch/c_run_affected.sh` 跑了 7 个旧文件
  （`test_private_daily_items` 14/11.9s OK、`test_life_v1_integration` 56/429.5s OK、
  `test_life_stay_early_exit` 2/41.8s OK、`test_one_day_life` 只有 `EXIT_CODE=0` 无 `Ran` 行、
  `test_one_day_life_w05r` 33 个点后被终止）。处置：只对 WSL 内 `23166`（bash 脚本）与其子
  `24919`（`python3 tests/test_one_day_life_w05r.py`）发 SIGTERM，未碰同机 phone `serve`
  与 Supply/World，未删任何 fixture/pyc/socket。
- 决策门禁（repo cwd）：
  `node --experimental-strip-types …\verify-agent-note-tree.ts` → `ok: 46 note(s) tree and relative links verified`，
  `…verify-agent-note-format.ts` → `ok: 46 note(s) verified`，均 exit 0。
- 契约与结果：[C 侧机制契约](../../../../docs/engineering/rc1-subject-materials-contract.md)、
  [C 侧交付结果](../../../../docs/engineering/rc1-subject-materials-core-result.md)。
- 相邻既有决定：[阅读停点与私人笔记契约](./2026-10-22-rc1-reading-owner-phone-reading.md)、
  [手机实例是主体 Life 的唯一 live 绑定来源](./2026-10-23-rc1-phone-life-subject-binding.md)、
  [单一写者隔离](../../implemented/process/2026-10-16-single-writer-isolation.md)。
