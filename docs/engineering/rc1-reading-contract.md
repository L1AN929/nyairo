# rc1 阅读工作台契约（C 侧冻结，P / U 只读）

本文件由 C（唯一写者）冻结，供 P（`components/phone/**`、`scripts/phone.py`、`chiyo_bundle/phone_tool.py`）
与 U（`components/phone/web/**`）逐字对齐。C 的实现是
`components/life/scripts/phone_reading.py`（`PhoneReading`）；它只使用**现有** owner：

* 阅读正文 = Life Supply `ManagedArtifact`（实例里的 `reading_artifact_id`），经
  `life_artifact_port.LifeArtifactPort.read_artifact / read_head / commit_version /
  resolve_operation / operation_row`；
* 取得证据 / 停点 / 笔记 / 疑问 = `PrivateDailyItems`（`private_items_artifact_id`）那一份 typed 文档。

没有第二份 store，没有 phone 内容数据库，没有正文缓存，没有页码进度文件。

## 1. 信封

C 的每个方法返回同一个形状（`status` 取值与 `components/phone/service.py` 的同名常量逐字一致）：

```json
{"ok": true,  "status": "applied",          "data": {...}, "http_status": 200}
{"ok": true,  "status": "already_applied",  "data": {...}, "http_status": 200}
{"ok": false, "status": "rejected",    "reason_code": "READING_HEAD_CONFLICT", "data": {...}, "http_status": 409}
{"ok": false, "status": "unknown",     "reason_code": "READING_OPERATION_UNKNOWN", "data": {...}, "http_status": 202}
{"ok": false, "status": "unavailable", "reason_code": "SUPPLY_UNAVAILABLE", "data": {...}, "http_status": 503}
{"ok": false, "status": "not_found",   "reason_code": "READING_OPERATION_NOT_FOUND", "data": {...}, "http_status": 404}
```

* 成功写一律 `status: "applied" | "already_applied"`，数据在 `data` 里。
* GET 沿用手机既有 `ok/status` 惯例：读成功就是 `status: "applied"`（即手机 `STATUS_OK`）。
* `http_status` 是**给 P 的提示**，不是 C 发 HTTP；P 可以继续用现有 `http_status_for(status)`
  （`applied/already_applied→200, rejected→409, unknown→202, unavailable→503, not_found→404`），
  也可以直接用这个提示值。
* **请求形状错误仍然是 400，由 P 在调用 C 之前挡**（`page` 不是非 bool 整数、字段缺失、
  `Content-Length` 超 64 KiB 等）。C 自己也会再拒一次（`PAGE_INVALID` 等，`http_status: 400`），
  那是纵深防御，不是 P 可以省掉前置校验的理由。
* C 不自己写归因日志；POST 的 `record_operation` 仍由 P 记。

## 2. 五个方法（`PhoneReading`）

```python
PhoneReading(port, *, reading_artifact_id, private_items_artifact_id, workspace_id, grant_id)
```

`port` 必须是 `LifeArtifactPort`；`workspace_id` / `grant_id` 必填（`grant_id=None` 时读路径照旧可用，
写路径回 `READING_GRANT_REQUIRED`）。C 不提供"猜哪份 artifact 是阅读材料"的入口。

| 方法 | 对应端点 |
|---|---|
| `material(page=None)` | `GET /api/phone/reading/material?page=N` |
| `replace_material(content, expected_revision, operation_key)` | `POST /api/phone/reading/material` |
| `save_position(page, source_revision, expected_head, operation_key)` | `POST /api/phone/reading/positions` |
| `annotate(kind, page, source_revision, expected_head, operation_key, title=None, body=None, note=None)` | `POST /api/phone/reading/annotations` |
| `operation(operation_key)` | `GET /api/phone/reading/operations/<key>` |

旧的 `GET /api/phone/reading` 不动：仍返回既有 `items` / `understanding`，不得在那里塞正文。

## 3. `GET /api/phone/reading/material`

`page` 可选；**默认 = 当前版本最近一次显式保存的停点，否则 1**。"当前版本"指
`source_revision == 材料真实 head`；旧版本的停点不会被偷偷搬到新版同一页。

`data`：

| 字段 | 含义 |
|---|---|
| `available` | 这份实例是否真有可用阅读材料 |
| `artifact_id` | 材料 artifact id（实例里的那个） |
| `source_id` | `artifact:<artifact_id>`（不叠第二层前缀） |
| `source_revision` | 材料真实 head `version_id` |
| `version_number` | 材料真实 head 版本号 |
| `title` | artifact 标题 |
| `page` / `pages` | 当前页 / 总页数（`%%PAGE%%` 分页；无标记 = 1 页） |
| `text` | **该页真实正文** |
| `content_available` | 该页正文非空 |
| `private_head` | 私人事项真实 head `version_id`，供下一次 CAS |
| `bookmark` | `{page, source_revision, needs_reread, needs_reread_reason, position_id, set_at}` 或 `null` |
| `annotations` | `[{id, kind:"note"/"unclear", title?, body?, note?, source_id, source_revision, page, created_at, stale, needs_reread}]` |
| `understanding` | 恒 `"UNKNOWN"` |
| `private_items_status` | `"available"` / `"unavailable"`（私人文档读不到时诚实降级，材料阅读本身不受影响） |

约束：

* **GET 零写**：不记录取得、不保存位置、不写笔记、不写文件、不创建材料、不调用任何 commit。
* 没有绑定材料 / 材料不存在 / 正文为空 → `status:"unavailable"`，`reason_code:"READING_MATERIAL_UNAVAILABLE"`，
  `data.available=false`、`content_available=false`、`bookmark=null`、`annotations=[]`。**不补演示文本**。
* `page` 越界 → `PAGE_NOT_PRESENT`；`page` 不是非 bool 整数 → `PAGE_INVALID`。
* `annotations` 只含**这份材料**（`source_id` 相同）的批注；旧版本的批注保留它自己的
  `source_revision` 并带 `stale:true` / `needs_reread:true`。`note` 与 `unclear` 是两种类型，
  **不把普通批注说成"没看懂"**。

## 4. `POST /api/phone/reading/material`

请求字段（P 的白名单）：`{content, expected_revision, operation_key}`。

* `content` 是**操作人显式给的正文**，不是抓取结果；只替换实例绑定的那一份材料，旧内容留在 Supply 版本历史里。
* 边界（全部按 UTF-8 bytes）：正文 1..24000、每页 ≤12000、最多 64 页；空正文 / 空白页 /
  超界一律拒绝，**不截断**；P 也不能声明 `title`、来源回执或理解。
* `expected_revision` 必须等于材料真实 head；过期 → `READING_REVISION_CONFLICT`。
* 成功后 `data` 含 `source_revision`（**这次落的那个** version）、`version_number`、`pages`、`page:1`、
  `page_bytes`、`request_chars` / `request_bytes`（这次请求的正文）、`material_current_chars`、
  `revision_current`、`material_current_revision`、`bookmark`（**旧停点，`needs_reread:true`**）、
  `private_head`、`title`、`understanding`。
  **不自动创建 acquisition / bookmark**，不改 `understanding`。
* 材料替换与 PDI 停点是**两个独立事务**：材料成功、停点没动是正常结果，反之亦然。
* **同一个键不是"当前正文恰好相同"就算成功**。材料键必须对上 owner 自己的操作行：
  `artifact_id` 是这份材料、`operation_kind == "COMMIT_MARKDOWN"`、`request_digest` 等于本次请求
  （artifact + `expected_head` + 正文）的摘要。因此：
  * 借一个停点键 / 笔记键 / 别的 artifact 的键来导入 → `READING_OPERATION_KEY_REUSED`（零写），
    哪怕当前 head 的正文和这次请求一字不差；
  * 同一个键换正文或换 `expected_revision` → `READING_OPERATION_KEY_REUSED`；
  * 真重放（原键 + 原 `expected_revision` + 原正文）→ `already_applied`，`detail:"OPERATION_KEY_REPLAY"`，
    `source_revision` 是它**当初**落下的版本，`revision_current:false` 明确表示"当前 head 已不是它"，
    所以既不会反向误判 `REUSED`，也不会声称"当前正文仍等于原请求"。
  * owner 的操作行读不出来（不是"没有"）→ `READING_OPERATION_UNKNOWN`，绝不当成没落过。

拒绝码：`CONTENT_INVALID` / `CONTENT_EMPTY` / `CONTENT_TOO_LARGE` / `TOO_MANY_PAGES` /
`BLANK_PAGE` / `PAGE_TOO_LARGE`（400）、`READING_REVISION_CONFLICT` / `READING_OPERATION_KEY_REUSED` /
`READING_GRANT_REQUIRED`（409）、`READING_OPERATION_UNKNOWN`（202）、
`READING_MATERIAL_UNAVAILABLE`（503）。

## 5. `POST /api/phone/reading/positions`

请求字段（P 的白名单）：`{page, source_revision, expected_head, operation_key}`。

* `page` 严格整数非 bool；`source_revision` 必须是当前材料 head；`expected_head` 必须是当前
  私人事项 head，并且**会被真的用作那一次 CAS 的前置条件**（不是"先读一遍确认、再拿新 head 提交"）。
* 服务**真读**该页（owner 的 `read_artifact`），然后把 `source_acquisition`（`obtained={pages:[N],chars:L}`，
  `receipt_ref=read:<artifact>:<revision>:page-N`，`excerpt=text[:200]`）与 `reading_position`
  在**同一个 PDI 版本**里原子提交。不存在"只记了取得、没记位置"却报成功的状态。
* 成功后 `data`：`page`、`pages`、`source_revision`、`revision_current`、`material_current_revision`、
  `version_number`、`title`、`obtained`、`receipt_ref`、`acquisition_id`、`position_id`、`item_id`、
  `private_head`、`understanding:"UNKNOWN"`、`operation_key`、`effect_state`、`owner_status`、
  `version_id`、`detail`。
* 可以**回到前页**并保存：默认恢复的是最近一次显式保存的停点，不是最高页。
  （`private_daily_items.reading_position_page` 仍是"下一页候选"用的最高页 helper，C 不用它冒充恢复点。）
* 只翻页不保存：GET 不会产生停点。
* **原键先在私人文档里回读**，再看版本：旧版本上已经成功过的停点，重放返回 `already_applied`
  （`detail:"OPERATION_KEY_REPLAY"`，`revision_current:false`、`obtained:null`——不声称当前正文还是那一页），
  不会因为材料后来换版就被误报成冲突。新键 + 旧版本仍然是 `READING_REVISION_CONFLICT`。

拒绝码：`PAGE_INVALID`（400）、`PAGE_NOT_PRESENT` / `READING_REVISION_CONFLICT` /
`READING_HEAD_CONFLICT` / `READING_OPERATION_KEY_REUSED` / `READING_GRANT_REQUIRED`（409）、
`READING_OPERATION_UNKNOWN`（202）。

## 6. `POST /api/phone/reading/annotations`

请求字段（P 的白名单）：`{kind, page, source_revision, title?, body?, note?, expected_head, operation_key}`。

* `kind` 只能是 `"note"` 或 `"unclear"`，字段按 kind **白名单**：
  * `note` 需要 `title` + `body`（title ≤200 chars / body ≤8000 bytes）；
  * `unclear` 需要 `note`（≤8000 bytes）；
  * 给了不属于该 kind 的字段 → `ANNOTATION_FIELD_NOT_ALLOWED`；其它 kind → `ANNOTATION_KIND_INVALID`。
* 必须是**当前真实材料页**（`source_revision == head`，`page ≤ pages`），而且**已经取得过这一版这一页**
  （存在 `available` 的 acquisition，其 `obtained.pages` 含该页）；否则 `PAGE_NOT_ACQUIRED`，
  UI 应提示"先保存停点"。
* `note` 复用 MEMO，并加可选 `reading_anchor:{source_id, source_revision, page}`（元数据白名单字段，
  旧 memo 没有锚点照旧可读可改，改正文不抹锚点）；`unclear` 复用既有独立类型 `unclear_marker`。
  note 的条目 id 由**操作键**派生（`memo:<hash>`），unclear 的条目 id 也由操作键派生
  （`unclear:<hash>`），所以**同一句话在不同页是两条记录**，不会被合并成一条，也不会出现
  "第二个键没有归属"。
* 注释正文只进**私人读取面**（本 GET 与 `GET /notes`），不进 `/state`、不进通用 metadata。
  `GET /notes` 的元数据行只多一个 `reading_anchor`。
* 成功后 `data`：`kind`、`id`、`item_id`、`page`、`source_id`、`source_revision`、`revision_current`、
  `material_current_revision`、`anchor`、`private_head`、`understanding:"UNKNOWN"`、`operation_key`、
  `effect_state`、`owner_status`、`detail`。
* 同一把键改标题 / 改正文 / 改疑问文本 → `READING_OPERATION_KEY_REUSED`（零写）；原键同意图 →
  `already_applied` 并回原来的 `item_id`。

## 7. 操作键：动作 + 目标 + 参数 + 版本

* 一个 `operation_key` 绑定**一个**意图。同键同意图重放 → `status:"already_applied"`，
  不新落一版（即使调用方手里的 head/revision 已经过期）。
* 同键换动作（position ↔ annotation ↔ material）、换页、换 kind、改标题/正文/疑问文本 →
  一律 `reason_code:"READING_OPERATION_KEY_REUSED"`，409，**零写**。
* 私人文档侧的绑定是**整条请求的摘要**（`intent`：动作 + artifact + source + 版本 + 页 + kind +
  标题/正文/疑问文本的哈希）。摘要**不含正文本身**，随效果写进同一个 PDI 版本，重放时逐个字节比对；
  改一个字就是换意图。
* 判断顺序是硬性的：**先查已落的操作**（typed 文档里的 `reading_operations` 归属 → owner 的
  `lookup_operation` 行 → 最后才看 `expected_head`/`expected_revision`）。先判版本会把"原键重放"
  误报成冲突。`expected_head` 本身记在归属里作为**证据**，但不参与"是不是同一个意图"的判定——
  否则一次无关的私人事项写入就会让合法重试变成 `REUSED`。
* 最小操作归属字段：`{operation_key, action, kind?, page, source_revision, source_id, intent,
  expected_head, at, item_id, acquisition_id?, position_id?}`，最多 200 条（最旧先丢），
  不含正文，也不出现在 `metadata_view` 里。
* UNKNOWN 不等于失败、也不等于成功：`status:"unknown"`（202），`data.retryable:true` 表示
  owner 说"从未落地"，可以用**同一个键**重试；`data.retryable` 缺失/False 表示效果未确定，
  必须先查，不得换键重放。

## 8. `GET /api/phone/reading/operations/<key>`

`data` 分三种来源，都带 `operation_key`：

1. 私人文档里有归属 → `action`（`position`/`annotation`）、`kind`、`page`、`source_revision`、
   `item_id`、`record`（整条归属）、`effect:"APPLIED"`、`applied:true`、`artifact_id`=私人事项 artifact。
2. 只有 owner 的操作行 → `action:"material"`（这份材料的 `COMMIT_MARKDOWN`）或 `"supply"`（其它 artifact，
   例如私人文书自己的写），外加 `owner_operation`：
   `{artifact_id, artifact_is_reading_material, outcome, version_id, version_number,
   parent_version_id, version_created, revision_current, material_current_revision}`。
   **换版之后这个键仍然描述它当初落下的版本与 parent**，`revision_current:false` 只说明现在不是 head。
3. 都没有 → `effect:"NOT_LANDED"` → `status:"not_found"` + `READING_OPERATION_NOT_FOUND`；
   owner 说不清 → `status:"unknown"` + `READING_OPERATION_UNKNOWN`。

这是 Supply 面的查询，**不是**购物 operation 接口；P 不要把两者混用。

## 9. 隐私与不伪造

* 正文只在 `material(page)` 里按页给；`localStorage`/`sessionStorage` 不得持久化正文（U 的约束）。
* `metadata_view` / `/state` 不含 memo body、unclear note、acquisition excerpt；操作归属里只有哈希。
* `understanding` 恒 `UNKNOWN`：页码、批注、收藏都不会变成"懂了"；C 不提供任何 setter。
* 不做：LLM 调用、理解评分、网络抓取、Memory 写入、Body 新数学、World 新动词/书架容器/椅子接触状态、
  扣款、自动补读（停机不补演）。

## 10. 给 P / U 的一句话

P 只做：解析请求 → 建 `PhoneReading` → 调一个方法 → 按信封 `status` 转发（并把 POST 的归因
写进既有 `operations.jsonl`）；U 只做：把 `data.text` 用 `textContent` 显示、按 `page/pages` 翻页、
用 `bookmark` 恢复、用 `annotations.kind` 区分笔记与疑问、用 `revision_current` 说清"这是旧版停点"、
看不到 `content_available:false` 时说"没有正文"而不是"已读"。
