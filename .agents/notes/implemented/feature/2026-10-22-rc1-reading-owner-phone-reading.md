# Agent Note: 阅读工作台——手机能真的读一页、真的存停点、真的在那一页写私人笔记

Status: implemented

## Problem

rc1 的阅读链在 Life/Supply 侧早就能跑（`one_day_life.read_page` + `tests/test_life_v1_restart.py` 的两进程重启用例），
但**手机上没有任何一条能用的路**：

1. `GET /api/phone/reading` 只回 acquisitions/positions 的元数据（`PhoneService.reading()` 那一版），
   **一个字正文都没有**，也没有任何 `POST /reading`；手机上看不到"在读什么"，更读不了下一页。
2. **正文没有 owner 之外的第二条路**：正文在另一个 ManagedArtifact 里（实例的 `reading_artifact_id`），
   `PrivateDailyItems` 只存"取得证据 + 位置"，`MAX_EXCERPT_CHARS=480` 明确拒绝把来源正文塞进事项文档。
3. **停点被误解成"最高页"**：`private_daily_items.reading_position_page()` 是给"下一页候选"用的最高页
   helper（`autonomous_life_cycle` 的下一步就靠它），它不是"最近一次显式保存的停点"。
4. **段落锚定笔记没有 schema**：memo 没有 source/revision/page；而 `mark_unclear` 恒表示"读不懂"，
   拿它存普通批注是撒谎。
5. **一次阅读会变成两笔写**：取得与位置分两次提交时，第二次失败就留下"取得记了、位置没记"的半成功。
6. **GET 写盘的风险**：阅读页一旦在 GET 里补 stop point，刷新节奏就变成业务时钟。

第一版实现之后，父级定点核对又发现四个**契约级**缺陷，都在同一批文件里修掉（不是重开泛化 review）：

7. **同键换载荷被当成重放**：归属只比 `action/page/revision/kind`，于是 `annotate` 用同一把键
   把笔记正文换一段照样 `already_applied`。
8. **同句疑问跨页会合并**：`mark_unclear` 的条目 id 由"source+revision+note"派生，没有页码，
   于是同一句"没看懂"写在两页会成一条，第二个键连归属都没有，`record.item_id` 也没落盘。
9. **借键冒充材料导入**：材料替换在任何"已落 key"下只比当前 head 正文，于是拿一个停点/笔记键
   导入一段和当前正文相同的文本会成功——它既不是这次导入，也不该成功。
10. **私有 CAS 有 TOCTOU**：先单独校验 `expected_head`、再由 `_commit` 读一次新 head 去提交，
    预检查之后 head 变了照样写成功。
11. 另外：`save_position`/`annotate` 先判材料版本再查原键，旧版上已经成功过的操作会因为材料换版
    被误报成冲突。

## Decision

只由 C 一个 writer，新增 `components/life/scripts/phone_reading.py` 的 `PhoneReading` facade，
对 `private_daily_items.py` 与 `life_artifact_port.py` 做窄改：

1. **正文只有 owner**：`material(page)` 每次真读 `read_artifact`，按 `%%PAGE%%` 切分（无标记 = 1 页），
   只返回该页 `text`；GET 不调任何 commit、不建材料、不记录取得、不写文件。缺材料是
   `unavailable` + `READING_MATERIAL_UNAVAILABLE`，不补演示文本。
2. **一次阅读 = 一个版本**：新增 `PrivateDailyItems.record_page_read()`，其 mutate 复用既有的
   `plan_page_read()`，把 `source_acquisition` 与 `reading_position` 放进**同一次** `_commit`；
   `obtained={pages:[N],chars:L}`、`receipt_ref=read:<artifact>:<rev>:page-N`、`excerpt=text[:200]`、
   `understanding` 恒 `UNKNOWN`。
3. **停点 = 最近一次显式保存**：`_bookmark()` 按 `set_at` 取最近一条 position（不是 max page），
   GET 的默认页 = 当前 head 版本的那条停点，否则第 1 页；旧版本停点不搬到新版，只带
   `needs_reread`/`source_revision`。`reading_position_page()` 保持原样，只服务"下一页候选"。
4. **段落锚点是元数据**：memo 增可选 `reading_anchor:{source_id,source_revision,page}`
   （元数据白名单），旧 memo 无锚点照旧可读可改，`revise_memo` 只改正文所以不抹锚点；
   `unclear` 仍用既有 `unclear_marker`，两种批注在 GET 里带各自 `kind`。
5. **整条请求才是绑定**：私人事项侧的写把 `intent = sha256(动作 + artifact + source + 版本 + 页 +
   kind + 标题/正文/疑问文本)` 与效果写进同一版本的 `reading_operations`（**只存哈希，不存正文**，
   上限 200 条，不进 `metadata_view`）。重放比整条摘要；同键改标题/正文/疑问文本/换页/换 kind 一律
   `READING_OPERATION_KEY_REUSED` 零写。`expected_head` 作为证据记在归属里但不进摘要（它是会被
   无关写入推进的 CAS 头，算进去会把合法重试判成 REUSED）；材料侧的 `expected_revision` 参与判定。
6. **材料键核对 owner 自己的操作行**：`life_artifact_port.operation_row()`（只读转发既有
   `lookup_operation`）给出 kind / artifact / `request_digest` / 原结果；`replace_material` 要求
   artifact 是这份材料、kind 是 `COMMIT_MARKDOWN`、`request_digest` 等于本次请求（artifact +
   expected_head + 正文）的摘要。借停点/笔记/别的 artifact 的键 → REUSED；同键换正文 → REUSED；
   真重放 → `already_applied` 并描述**当初那个版本**（`revision_current:false`），既不被后续改版
   反向误判，也不声称当前正文仍等于原请求。
7. **私有 CAS 用调用者的 head**：`_commit(..., expected_head=...)` 先本地比对、再把**调用者的**
   `expected_head` 交给 owner 作为 CAS 前置条件；预检查后 head 变了照样被拒（`READING_HEAD_CONFLICT`
   并报出真实 head）。
8. **先查原键、再判版本**：`save_position`/`annotate` 先在私人文档里回读原键，再看材料版本；
   旧版上已成功的操作重放 → `already_applied`（`revision_current:false`、`obtained:null`），
   新键 + 旧版本仍 `READING_REVISION_CONFLICT`。同一句话在不同页是**两条**记录：
   note 与 unclear 的条目 id 都由操作键派生（`memo:<hash>` / `unclear:<hash>`），
   `mark_unclear` 因此新增可选 `marker_id`（不给就是原来的意图派生 id，旧调用零变化），
   归属里的 `item_id` 落盘，`operation(key)` 与重放都精确到条目与页。
9. **两个 owner 两笔事务**：`replace_material` 只 `commit_version`，不写 PDI、不建 acquisition/bookmark，
   旧停点保留且 `needs_reread:true`；材料与停点各自成功/失败，不假装全局事务。

## Alternatives considered

* **建一个 phone 阅读库（正文 + 页码 + 笔记一份 JSON/表）**。最强理由：手机要什么有什么，一次查询
  搞定，不用碰 Supply 的 CAS/UNKNOWN。否决：那是第二份正文与第二份进度真相，和 owner 必然漂移；
  更糟的是它会把"读到哪里"从 owner 变成一个可被 GET 顺手改写的缓存。
* **把正文塞进 `PrivateDailyItems` 的 excerpt**。最强理由：一处文档就够，读一次不再回 Supply。
  否决：`MAX_EXCERPT_CHARS=480` 与"文档不缓存来源正文"是既有定案，撤权后也真撤不掉。
* **复用 `mark_unclear` 存所有批注**。最强理由：位置+note 现成，零 schema 改动。否决：它恒表示
  "读不懂"，把"读到这段想到别的"写成"没看懂"是语义撒谎。
* **归属只记 action/page/revision/kind（第一版做法）**。最强理由：字段少、不用哈希、直观。
  否决：它挡不住"同键换正文/换标题/换疑问文本"——载荷变了照样 `already_applied`；改为整条请求
  的稳定摘要，正文只做哈希输入。
* **`mark_unclear` 继续用意图派生 id**。最强理由：不动 pdi 签名。否决：意图里没有页码，同一句
  "没看懂"写两页会合并，第二个键连归属都没有；改为手机路径传 `marker_id`（可选参数，旧调用不变）。
* **材料重放比"当前 head 正文"**。最强理由：一行比较，不用读 owner 的操作行。否决：会把借来的键
  （当前正文恰好相同）当成功，也会在后续改版后把原键反向误判成 REUSED；改为核对 owner 操作行的
  artifact/kind/`request_digest`。
* **材料重放去读历史版本文本**。最强理由：能逐字证明原效果。否决：Supply 只暴露 head 内容，
  没有历史版本读口；不为此扩服务或建平行 store，改用 owner 已存的摘要+结果（version/parent/outcome）
  描述原效果，并用 `revision_current` 明说"当前不是它"。
* **私有 CAS 先读 head 校验、再由 `_commit` 读新 head 提交**。最强理由：改动最小，pdi 内部读一次。
  否决：那正是 TOCTOU，预检查后 head 变了仍会覆盖；改为把调用者的 `expected_head` 交给 owner 校验。
* **用 `reading_position_page()`（最高页）当恢复点**。最强理由：一行调用。否决：最高页 ≠ 最近停点，
  "回到前页"会被谎报成"还在后面"。
* **材料替换时把旧停点搬到新版同一页**。最强理由：体验连贯。否决：新版内容可能完全不同，搬过去
  就是伪造读过；只保留旧停点并标 `needs_reread`。

## Consequences

* 手机（P）只需要 `PhoneReading` 五个方法 + 一封固定信封；UI（U）只需要
  `text/page/pages/bookmark/annotations/revision_current`。端点与字段冻结在
  `docs/engineering/rc1-reading-contract.md`，P/U 不改契约。
* 真实链路成立：`material()` 真读正文 → `save_position()` 一次落取得+位置 → `annotate()` 对**已取得的
  真实页**写 note/unclear → 换版后旧书签 `needs_reread` → 保存新版停点。
  `tests/test_phone_reading_owner.py` 16 个用例在真实 owner 上全绿（`Ran 16 — OK`，退出码 0），
  `tests/test_private_daily_items.py` 14 个既有用例复跑全绿（`Ran 14 — OK`，退出码 0）。
  笔记门禁按父级指定脚本跑：`verify-agent-note-tree.ts` / `verify-agent-note-format.ts`
  （cwd = 仓库根）都是 `ok: 44 note(s)`，退出码 0。
* 反例都有真实 owner 证据：同键改笔记正文/疑问文本 → REUSED 且正文未变；同句疑问跨两页 → 两个 id、
  两条 marker、query 各自精确；借停点/笔记键导入"与当前正文一字不差"的文本 → REUSED；
  材料改版后原键仍描述原版本、重放 `already_applied`、换 payload/换 expected_revision → REUSED；
  交错写让 head 在提交前变化 → `READING_HEAD_CONFLICT` 且我们一条都没落。
* 可重放：原键 + 旧 head 回读成功且版本号不变（测试直接比对 owner 的 `version_number`）。
* 代价与边界：`reading_operations` 是对 typed 文档的**加法**（`SCHEMA_VERSION` 仍 1、缺省即无、
  上限 200、无正文）；材料侧摘要是按 Supply 的规范 JSON 复算的（只读比对，若 Supply 改内部形状
  会失败关闭成 REUSED）；`unclear` 现在可由调用方给 id（旧调用不变）；阅读面自己的正文/注释上限
  （24000/12000/64 页、title 200、body & note 8000）与 `POST /notes` 的上限不同。
* 明确没做：不接真实实例、不起手机 HTTP 服务、不改 World/Body/economy/Supply/one_day/autonomous、
  不写 Memory、不调模型、不抓网络、不扣款、不加 World 动词或书架容器、不补读停机时段。
