# rc1 阅读工作台——C（核心）实现结果（含本轮定点返修）

Scope：施工单 `NYAIRO-rc1-阅读工作台与私人笔记-施工单.md` 的 **C（核心）** 写集，只此一份。
根：`D:\nyairo-rc1-body-backend`，branch `work/rc1-phone-world-body`，HEAD
`41e818faaa24115fbf43db2977b15585c04144af`（未 git add / commit / reset / push / tag）。
本轮**没有**接真实实例、没有起手机 HTTP 服务、没有网络、没有模型调用。

## 1. 实现的文件

| 文件 | 状态 | 内容 |
|---|---|---|
| `components/life/scripts/phone_reading.py` | 新增（1102 行） | `PhoneReading` facade：`material` / `replace_material` / `save_position` / `annotate` / `operation`；信封与稳定 reason 码；页码/正文/注释边界；意图摘要绑定；owner 操作行核对；逐页真读；GET 零写 |
| `components/life/scripts/private_daily_items.py` | 窄改（`git diff --stat`：+176/−14，无既有行为删除） | ① memo 可选 `reading_anchor` + 元数据白名单；② 阅读操作最小归属 `reading_operations`（与效果同版本、上限 200、无正文）；③ `record_page_read()`：取得 + 位置**一个版本**；④ `_commit(..., record=, expected_head=)`：调用者的 `expected_head` 就是那次 CAS 的前置条件；⑤ `mark_unclear(..., marker_id=)`：手机侧按操作键派生 id，旧调用不变 |
| `components/life/scripts/life_artifact_port.py` | 窄增（+24，只加一个**只读**方法） | `operation_row(operation_key)`：转发既有 `lookup_operation` 读 op，回 owner 的操作行（kind / artifact / `request_digest` / 结果）。其余一字未改 |
| `tests/test_phone_reading_owner.py` | 新增（842 行，16 个用例） | 真实 owner + 只有"答复丢失/owner 说不清"三处定点注入 |
| `docs/engineering/rc1-reading-contract.md` | 新增 | 冻结给 P/U 的信封、字段、码表、边界、不变量 |
| `docs/engineering/rc1-reading-owner-result.md` | 新增 | 本文件 |
| `.agents/notes/implemented/feature/2026-10-22-rc1-reading-owner-phone-reading.md` | 新增 | Problem / Decision / Alternatives / Consequences |

零写确认：`components/phone/**`、`components/world/**`、`components/supply/**`、`scripts/phone.py`、
`chiyo_bundle/phone_tool.py`、`one_day_life.py`、`autonomous_life_cycle.py`、`MANIFEST.json`、
`tests/test_private_daily_items.py` 正文、既有 `docs/engineering/rc1-room-*.md` / `v04-*.md`
全部未改（`git status` 里它们仍是进入时的 dirty 状态）。

## 2. 接口（与施工单逐字对齐）

```python
PhoneReading(port, *, reading_artifact_id, private_items_artifact_id, workspace_id, grant_id)
    .material(page=None)                                                    # GET  /api/phone/reading/material
    .replace_material(content, expected_revision, operation_key)            # POST 同上
    .save_position(page, source_revision, expected_head, operation_key)     # POST /api/phone/reading/positions
    .annotate(kind, page, source_revision, expected_head, operation_key,
              title=None, body=None, note=None)                             # POST /api/phone/reading/annotations
    .operation(operation_key)                                               # GET  /api/phone/reading/operations/<key>
```

信封：`{ok, status, reason_code?, data, http_status?}`；`status ∈ {applied, already_applied,
rejected, unknown, unavailable, not_found}`（与 `components/phone/service.py` 常量同值）。
`http_status` 只是给 P 的转发提示（400/409/202/503/404），C 不发 HTTP。

稳定 reason 码：`READING_MATERIAL_UNAVAILABLE`、`SUPPLY_UNAVAILABLE`、`SUPPLY_REFUSED`、
`READING_REVISION_CONFLICT`、`READING_HEAD_CONFLICT`、`READING_OPERATION_KEY_REUSED`、
`READING_OPERATION_UNKNOWN`、`READING_OPERATION_NOT_FOUND`、`READING_GRANT_REQUIRED`、
`READING_WRITE_REFUSED`、`READING_DOCUMENT_VERSION_MISMATCH`、`SOURCE_UNAVAILABLE`、
`PAGE_INVALID`、`PAGE_NOT_PRESENT`、`PAGE_NOT_ACQUIRED`、`PRIVATE_ITEMS_UNAVAILABLE`、
`ANNOTATION_KIND_INVALID`、`ANNOTATION_FIELD_NOT_ALLOWED`、`TITLE_INVALID`、`BODY_INVALID`、
`NOTE_INVALID`、`CONTENT_INVALID`、`CONTENT_EMPTY`、`CONTENT_TOO_LARGE`、`TOO_MANY_PAGES`、
`BLANK_PAGE`、`PAGE_TOO_LARGE`、`OPERATION_KEY_INVALID`、`EXPECTED_REVISION_INVALID`、
`EXPECTED_HEAD_INVALID`、`SOURCE_REVISION_INVALID`。

## 3. 不变量如何落地（含本轮返修）

1. **页码不是理解**：`understanding` 恒 `UNKNOWN`，没有 setter。
2. **一次阅读 = 一个版本**：`record_page_read()` 在**一次** `_commit` 里插入 `source_acquisition`
   （`obtained={pages:[N],chars:L}`、`receipt_ref=read:<artifact>:<rev>:page-N`、`excerpt=text[:200]`）
   与 `reading_position`（`understanding=UNKNOWN`）。测试断言 PDI `version_number` 只 +1、两条同时存在。
3. **整条请求才是绑定**（本轮返修①）：私人事项侧的写把 `intent = sha256(动作+artifact+source+
   版本+页+kind+标题/正文/疑问文本)` 存进同一版本的归属（**只存哈希，不存正文**）。重放时比整条摘要，
   所以同键改标题 / 改正文 / 改疑问文本 / 换页 / 换 kind 一律 `READING_OPERATION_KEY_REUSED` 且零写。
   `expected_head` 作为**证据**记在归属里，但不参与"是不是同一意图"的判定（否则一次无关的私人事项
   写入就会把合法重试判成 REUSED）；材料侧的 `expected_revision` 是参与判定的（它在 owner 的
   `request_digest` 里）。
4. **材料键核对 owner 自己的操作行**（本轮返修③）：`life_artifact_port.operation_row()` 只读转发
   `lookup_operation`，`replace_material` 要求 `artifact_id` 是这份材料、`operation_kind ==
   COMMIT_MARKDOWN`、`request_digest` 等于本次请求（artifact + expected_head + 正文）的摘要。
   于是"借停点/笔记/别的 artifact 的键"、"同键换正文"都不会因为"当前 head 正文恰好相同"而成功；
   旧材料键在后续改版后仍描述它当初落下的 version 与 parent（`revision_current:false`），
   不会反向误判 `REUSED`，也不声称当前正文仍等于原请求。
5. **私有 CAS 用调用者的 head**（本轮返修④）：`_commit(..., expected_head=...)` 先本地比对、再把
   **调用者的** `expected_head` 交给 owner 作为 CAS 前置条件；预检查之后 head 变了照样被拒。
   测试用真实交错写（另一次真实提交在本次 `commit_markdown` 到达 owner 之前落盘）证明：
   只有交错那一次落盘，我们的取得/位置一条都没有，回 `READING_HEAD_CONFLICT` 且报出真实 head；
   同一键用新鲜 head 重试仍可正常落地。
6. **旧版已成功的操作不被新版本反咬**：`save_position` / `annotate` 先把原键在私人文档里回读，
   再判材料版本。旧版上的停点/批注重放 → `already_applied`（`revision_current:false`、
   `obtained:null`），不会因为材料后来换版被误报冲突；**新键 + 旧版本仍是**
   `READING_REVISION_CONFLICT`（不放宽）。
7. **同一句话在不同页是两条记录**（本轮返修②）：note 与 unclear 的条目 id 都由**操作键**派生
   （`memo:<hash>` / `unclear:<hash>`），`mark_unclear` 因此新增可选 `marker_id`（不给就是原来的
   意图派生 id，旧调用零变化）；归属里的 `item_id` 落盘，`operation(key)` 与重放都精确到条目与页。
8. **材料替换与 PDI 独立**：`replace_material` 只调 `commit_version`，不写 PDI、不建
   acquisition/bookmark；旧停点保留且 `needs_reread:true`。
9. **GET 零写**：`material()` 只读；测试在 GET 前后比对两份 owner 的 `version_number` 与 `items()`。
10. **真实正文**：页面正文来自 owner 的 `read_artifact` 内容按 `%%PAGE%%` 切分；无标记 = 1 页；
    每页只给该页文本（测试断言第 2 页响应里不含第 1/3 页文字）。
11. **只读观察、不写世界**：facade 不碰 World/Body/economy。

## 4. 验证

命令（WSL，cwd = 仓库根，`PYTHONDONTWRITEBYTECODE=1`，夹具根保留不清理）：

```
python3 tests/test_phone_reading_owner.py -v      # Ran 16 tests, OK, 15.3s, 退出码 0
python3 tests/test_private_daily_items.py -v      # Ran 14 tests, OK, 12.9s, 退出码 0（我改了它，必须复跑）
```

笔记门禁（父级指定，cwd = 仓库根，**不是**等价检查）：

```
node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-tree.ts
  -> ok: 44 note(s) tree and relative links verified        (退出码 0)
node --experimental-strip-types C:\Users\28556\.agents\skills\write-notes-like-deepseek\scripts\verify-agent-note-format.ts
  -> ok: 44 note(s) verified                                (退出码 0)
```

逐用例（全部真实 owner 证据，除注明外）：

| 用例 | 证明 |
|---|---|
| `test_get_material_returns_the_real_page_and_writes_nothing` | 真实 head/版本号/正文/页数；GET 前后两份文档版本号与事项列表不变；第 2 页不含其他页正文；`page=4` → `PAGE_NOT_PRESENT`，`page=True`/`"2"` → `PAGE_INVALID`，均零写 |
| `test_no_material_is_an_honest_unavailable` | 未绑定材料 → `unavailable` + `READING_MATERIAL_UNAVAILABLE`，零写 |
| `test_page_marker_matches_the_marker_one_day_life_uses` | `PAGE_MARKER` 与 `one_day_life.PAGE_MARKER` 同值 |
| `test_save_position_is_one_version_and_restores_the_last_stop_point` | 一页 = 一版；acquisition+position 同版本；默认恢复最近停点；回第 1 页后默认也回第 1 页；`reading_position_page` 仍报 2（证明没用它冒充停点）；换 port 重开仍读同一停点 |
| `test_replaying_an_operation_key_never_mints_a_second_version` | 原键 + 旧 head → `already_applied`，版本号不变；材料原键 + 旧 revision 重放 → `already_applied`，材料只多一个版本；旧停点 `needs_reread:true` |
| `test_one_key_cannot_be_reused_across_kinds_or_pages` | 同键换 kind / 换页 → REUSED；借材料替换的键 → REUSED 且 `recorded_artifact`/`recorded_action` 指向材料 commit |
| `test_annotation_needs_a_real_acquired_page_and_keeps_its_anchor` | 未取得该页 → `PAGE_NOT_ACQUIRED`；note 的 `anchor` 精确；锚点在元数据、正文不在；`revise_memo` 不抹锚点；unclear 是独立类型；字段按 kind 白名单 |
| `test_new_material_version_marks_the_old_stop_point_for_a_reread` | 换版后旧书签 `needs_reread:true`、旧批注 `stale:true`、GET 默认回第 1 页、旧版写批注 `READING_REVISION_CONFLICT`、新版停点+批注可用 |
| `test_revision_head_page_and_import_bounds_are_refused_without_writing` | 过期 revision / 过期 head / 越界页 / bool / 字符串页全部拒绝且零写；正文边界 6 种拒绝码；无 grant 时读可用、写 `READING_GRANT_REQUIRED` |
| `test_unknown_answer_that_landed_is_reconciled_and_a_lost_one_stays_unknown` | **注入**：落盘但答复丢失 → 对账成 `applied`（`RECONCILED_AFTER_UNKNOWN`），只一版；未落盘 → `unknown` + `retryable:true`，同键重试才 `applied` |
| `test_operation_query_reads_the_owner_and_keeps_unknown_distinct` | 私人归属 → `action/record.position_id`；材料键 → `action:"material"` + `owner_operation.version_id`；**注入** owner 说不清 → `unknown`（不是成功也不是不存在），查询零写 |
| `test_the_same_key_with_a_changed_payload_is_refused_not_replayed`（新） | 同键改 note 正文 / 改标题 → REUSED，版本号不变，memo 正文仍是第一版；同键改 unclear 文本 → REUSED |
| `test_the_same_question_on_two_pages_keeps_two_records`（新） | 同句疑问在 1/2 页 → 两个不同 id、两条 marker（position 1 与 2）；`operation()` 的 `item_id`/`page` 各自正确；重放回原 `item_id`；GET 给出两条 unclear |
| `test_a_borrowed_key_cannot_import_the_material_with_identical_content`（新） | 用停点键 / 笔记键导入**与当前 head 一字不差**的正文 → REUSED（`recorded_action` position/annotation），材料版本号不变；该键的 owner 行属于私人事项 artifact |
| `test_an_old_material_key_still_describes_its_original_version`（新） | v2 键在 v3 之后：`operation()` 回 v2 的 version/parent/`revision_current:false`/当前 head；原 key + 原 revision + 原正文重放 → `already_applied` 且 `source_revision` 仍是 v2；换正文 / 换 expected_revision → REUSED；材料仍 3 版 |
| `test_a_head_that_moves_after_the_precheck_is_still_refused`（新） | 真实交错写：只有交错那一次落盘，`READING_HEAD_CONFLICT` + 真实 head，取得/位置零条；同键换新鲜 head 重试可落地 |

格式/语法：`ast.parse` 三文件 OK；格式检查（无 tab、无 >100 列行）0 issue。

## 5. 限制与偏差（父级需要知道的）

1. **`reading_operations` 是加法**：typed 文档新增顶层可选键（缺省即无），`SCHEMA_VERSION` 仍 1，
   `decode_document` 不做严格字段校验，旧文档照常可读；读取一律走 `pdi.reading_operation()`。
   它与效果同版本、含 `intent` 哈希/页码/版本/条目 id、无正文，不进 `metadata_view`，上限 200 条。
2. **材料侧摘要按 Supply 的规范 JSON 复算**：`_commit_request_digest()` 复现
   `artifact_operations.request_digest`（`{artifact_id, expected_head, content, operation_kind,
   operation_payload}` 的 sha256）。它只与**读**有关（比对已存摘要），不写任何东西。若 Supply 将来
   改这段内部形状，比对会失败关闭（报 REUSED 而不是误判成功）；本轮用真实 owner 钉住了当前形状
   （`test_an_old_material_key_still_describes_its_original_version` 与借键反例）。
3. **`expected_head` 不进意图摘要**（只作证据）：这是刻意的，因为它是私人事项的 CAS 头，
   会被无关写入推进；把它算进"同一意图"会让合法重试变 REUSED。材料侧的 `expected_revision`
   则参与判定（在 owner 摘要里）。两处行为都有真实 owner 用例覆盖。
4. **`unclear` 的条目 id 改为按操作键派生**（`mark_unclear` 新增可选 `marker_id`）：手机这条路因此
   不会把"同一句话在另一页"合并；**不带 `marker_id` 的旧调用仍是意图派生 id**，语义未变。
5. **材料侧没有历史版本文本读口**：服务只暴露 head 内容，所以"原效果"用 owner 的操作行
   （version/parent/outcome）描述，不声称当前正文等于原请求（`revision_current` 明示）。
   没有为此建任何平行 store。
6. **正文/注释边界是本面自己的**：正文 24000 / 页 12000 / 64 页 / title 200 / body & note 8000；
   手机笔记那条路（`MAX_TITLE=80`、`MAX_BODY=4000`）未改，P 若想统一必须自己选。
7. **P/U 尚未接线**（写集分工）：`PhoneReading` 还没被 `PhoneService` 调用，UI 还没阅读屏；
   契约里的端点、字段、reason 码是 P 必须逐字使用的形状。
8. 测试夹具根（`/tmp/nyaw05r.jdK3q/rc1-reading-*`）与 runner 脚本留在会话 scratch，按纪律不删除；
   仓库内无 `__pycache__` 写入（全程 `PYTHONDONTWRITEBYTECODE=1`）。

## 6. 失败与返修记录（原始日志，未删）

第一轮（首版实现）`Ran 11 — FAILED (failures=4, errors=2)`，报错原文：

    FAIL test_get_material...: 'artifact:a2c4...' != 'artifact:artifact:a2c4...'
    FAIL test_annotation...:  anchor source_id 期望多一层前缀（同上，测试侧写错了前缀）
    FAIL test_save_position...: reading_position_page(...) -> None（测试用了双层前缀的 source_id）
    FAIL test_one_key...: 'EFFECT_RECEIPT_FOUND' != 'OPERATION_ROW_FOUND'
    ERROR test_operation_query...: TypeError: _not_found() got multiple values for argument 'reason'
    ERROR test_unknown...: TypeError: _not_found() got multiple values for argument 'reason'

根因：前三条是**测试**把 `artifact:` 又拼了一层（owner 给的 id 本身已带前缀）；后三条是实现问题——
`operation()` 里的 `data["reason"]` 与 `_not_found(reason=...)` 形参重名。修完 `Ran 11 — OK`。

第二轮（按父级定点核对返修后、测试尚未同步新字段与新增反例）`Ran 11 — FAILED (failures=1, errors=2)`，
报错原文：

    ERROR test_one_key...: KeyError: 'recorded_via'           （新语义用 recorded_artifact/recorded_action）
    ERROR test_revision_head...: KeyError: 'private_head'     （HEAD_CONFLICT 包没带真实 head）
    FAIL  test_operation_query...: 'unavailable' != 'unknown' （owner 行读不出来被映射成了 503）

根因：第 1 条是测试断言旧字段；第 2 条是真的缺口——CAS 冲突时没有回报真实 head，已补进
`_conflict_from`；第 3 条是实现把"owner 说不清"当成了"供应不可达"，已改成 `operation_row` 抛
typed refusal、由 `_owner_row(strict=…)` 区分"未知"与"没落过"。修完并补齐 5 个反例：`Ran 16 — OK`。
