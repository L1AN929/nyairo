# Agent Note: v0.4 私人阅读的有限内容响应与阅读批注纯计划（note / unclear / no_change）

Status: implemented

## Problem

0.4「私人读写实践」要的产品效果是：主体在**已有的真实阅读活动**里取得一页，可以（不是必须）留下一条私人笔记、
一个疑问，或者什么都不记；取得、停点与这条可选批注落在**同一个 PDI 版本**里，下一轮读视图能看见真实批注计数
和锚点，跨进程还能接着原活动读。但当前树只到「她能读到真实的一页、能存真实停点」：

1. **没有任何地方允许内容模型为她产出私人文字。** 作品 writer（`model_continuation_writer`）只会续写作品，
   而且强制"最小修改"——它既不能写"这页让我停了停"，也不能诚实地什么都不写。阅读步骤里没有任何
   调用内容模型的入口，所以「一页 → 一条私人笔记」这条链根本不存在。
2. **「不记录」不可表达。** 旧作品 writer 的指令是"照常给出一个最小的改动，不要拒绝"，把"必须产出"写进了
   prompt。私人阅读恰恰要允许安静：这一页不需要留下东西，也是合法结果。没有 `no_change`，模型只能在
   "编一句"和"失败"之间选。
3. **批注没有纯计划。** 手机面（`phone_reading.annotate`）能写 note/unclear，但它自己读材料、自己判版本、
   自己算意图摘要；Life 侧要"在一次 AR-0 提交里同时落取得、停点、批注"，需要的是**给文档、还文档**的纯函数，
   不是第二个 facade，也不能为此新建库或第二份进度真相。
4. **失败必须留成失败。** 旧路里模型失败与"没有值得写的"最容易混成一句"这次没写"；一旦允许 `no_change`，
   就必须把"模型说不记"和"模型调用/解析失败"分得干干净净，否则报告会把失败写成安静。

## Decision

只由 A 一个 writer，新增 `components/life/scripts/subject_reading_response.py`，并对
`private_daily_items.py` 做一次窄改（一个新纯函数 + 三个常量组 + `__all__`），接口逐字按施工单 A：

1. **一次响应只有三种意图**：`ModelReadingResponse.respond(context, *, observed_at)` 只接受
   `note` / `unclear` / `no_change`。`note` 必须同时有非空 `title`(<=200) 与 `body`(<=1200)；
   `unclear` 必须有非空 `note`(<=1200)；`no_change` **不许带任何正文**（`title`/`body`/`note` 只要
   非空就拒绝，空串只当模板残留归一化成 `None`）。超长**不截断**，直接失败。
2. **失败是真的失败**：没有 client 构造即 `MODEL_CLIENT_REQUIRED`（B 报 NOT_CONFIGURED，不许伪造内容）；
   模型调用失败 `MODEL_CALL_FAILED:<failure_class>`；非 JSON/空/非对象分别是
   `MODEL_OUTPUT_NOT_JSON` / `MODEL_OUTPUT_EMPTY` / `MODEL_OUTPUT_NOT_AN_OBJECT`。全部抛
   `ReadingResponseError`，绝不返回替身正文。
3. **请求边界就是隐私边界**：`context` 只认 `source_id`/`source_revision`/`page`/`text`/
   `previous_page`/`total_pages` 六个键，多一个键就拒绝（多一个键就多一条可能被送进模型的私人数据）；
   正文按 `max_page_chars`(6000) 截进 prompt 并显式报 `page_text_truncated`；空页
   （`READING_TEXT_REQUIRED`）**不调模型**。模块不留 `raw` 输出，`describe()` 只有配置、次数、
   长度和一个错误码，正文与笔记一个字都不留。
4. **批注是纯计划**：`private_daily_items.plan_page_annotation(document, *, artifact_id, source_id,
   source_revision, page, response, operation_key, now) -> {content, changed, item_id, kind,
   operation_key}`。它自己**按页**核实真实取得：`_annotation_acquisition` 在同一 source+revision 的取得里只挑
   item_type 正确、`available` 真为真、且自己的 `obtained["pages"]` **真的覆盖当前页**的那一条（取最近一条）；
   共享的 `_available_acquisition` 不借用（它答的是"停点能不能指这一版"，并且返回该版第一条）。
   一步最多加**一条**：`memo`（带 `reading_anchor:{source_id, source_revision, page}`）或 `unclear_marker`；
   `no_change` 零条目、`content` 与输入逐字节相同、`changed=False`、`item_id=None`，停点与
   `understanding` 一个字节都不动。
5. **整条意图幂等**：意图摘要 = 全部请求绑定（action/artifact/source/revision/page/kind/
   title/body/note）的 `sha256`，形如 `reading-intent:<32 hex>`（与手机面同形、**只存哈希不存正文**），
   和效果一起写进同一版本的 `reading_operations`（上限仍 200）。**所有 kind 都先查原键**：同键同意图 →
   `changed=False` + 原条目；同键异意图、偷来的键（记录里没有 intent 摘要）、以及"已落过 note/unclear 的键
   再来说 no_change"→ `READING_OPERATION_KEY_REUSED` 拒绝（`no_change` 只接受全新键，且仍然零写）。
   条目 id 只由操作键派生（`memo:<16>`/`unclear:<16>`），所以重试落在同一行；归属掉出窗口时也从条目本身核对内容。
6. **不再有第四种写者**：手机面、Life 侧、作品 writer 都还在各自原位；本模块不读 socket、不提交、
   不碰 `metadata_view` 之外的投影，提交与 CAS 仍由调用方（B）用自己的 `expected_head` 完成。
   PDI 侧刻意**不 import 模型侧模块**（文档 writer 的 import 图里不该有模型 client），两份长度边界
   由测试钉住相等。

## Alternatives considered

- **让作品 writer 顺便写阅读笔记（复用 no_change/append 同一套）** —— 最强理由是"一个内容模型入口就够，
  少一套 prompt 和预算"。否决原因：作品续写的语义是"把这件作品往下写"，它的失败/成功直接牵动 owner 版本、
  进度与经历；把阅读笔记塞进去会让"读一页"变成"改作品"，正是旧缺陷（阅读 CONTINUE 误提交作品版本）的同类。
  现在两者各有自己的 writer，共用同一个 client/transport 形状，但互不代写。
- **给 PDI 新增第四种 item 类型（`reading_note`）** —— 最强理由是"私人笔记有自己的字段，语义更干净"。
  否决原因：既有定案已经把 note 做成带 `reading_anchor` 的 `memo`、把疑问做成 `unclear_marker`，手机面、
  元数据白名单与 `full_text` 都按这两条路走；新增类型等于让同一件事有两份 schema 与两个读口，还会把
  `SCHEMA_VERSION` 推上去。现在纯计划只产生既有类型，`reading_anchor` 仍是元数据。
- **让 `plan_page_annotation` import 模型侧的 `normalise_response` 复用校验** —— 最强理由是"校验只有一处，
  永不漂移"。否决原因：PDI 是所有私人读写（含手机面）都 import 的底层 typed 文档，把模型 client 拉进
  它的 import 图是不可接受的耦合。现在两边各留一份规则，由 `test_the_reader_bounds_match_the_response_parser`
  把常量与 kind 元组钉成相等；谁改一边测试就红。
- **超长时截断（像作品 writer 那样 `[:max] + "…"`）** —— 最强理由是"模型常常多写一点，截断比失败友好"。
  否决原因：截断后的笔记不是她写的那句话，等于对"她到底写了什么"说谎；PRD 要求"完整意图"可核对，
  静默改内容会直接破坏意图摘要与重放判定。现在超长一律拒绝，B 可以把这次当失败重试或放弃这一页。
- **空页也照常问模型，让它自己决定 no_change** —— 最强理由是"少一条分支，行为统一"。否决原因：给一个
  空页面只会换来编造（或一句"这页什么都没有"的假笔记），而且会白白消耗一次 request 预算。现在空页在
  模块边界就 `READING_TEXT_REQUIRED` 拒绝、一次模型都不调，B 应跳过内容请求但仍保存真实取得/停点。
- **`no_change` 也写一条归属记录** —— 最强理由是"同一把键以后被拿来写批注时能被认出是换意图"。
  否决原因：`no_change` 没有任何效果可归属，为它落一条记录会让 `content` 变化（B 就会多提交一个空版本），
  与"no_change 一个字节都不改"直接冲突。现在它零写；B 的键按"响应意图"生成，正常流程不会撞键。

## Consequences

- **收益**：私人阅读第一次有了**唯一**的内容模型出口，且三种意图的合法性、长度、越权字段与"no_change 不许带
  伪正文"都在解析层被钉死；纯计划把"取得/版本/撤权是否真实"与"一步最多一条""同键幂等""异意图拒绝"
  收进一个函数，B 只需要拿 `content` 去 CAS；私密正文不进 `describe`、不进归属记录（只有哈希）、
  不进任何 facts 键；`understanding` 仍是 `UNKNOWN`——读一页不等于读懂。
- **代价与已知上限**：① 全部断言基于注入替身，真实模型一次未调，`no_change` 的真实行为还没有证据；
  ② 纯计划没有 owner 级链路证据（本轮不需要 Supply），真实 owner 上"取得+停点+批注同一版本、跨进程续接"
  要等 B 的同实例链路；③ 两份长度边界是刻意镜像，改上限必须同时改 `subject_reading_response` 与
  `private_daily_items`（测试会拦）；④ 空页一律拒绝，B 必须自己区分"空页不调模型"和"模型失败"；
  ⑤ `no_change` 不写归属，所以"先 no_change 再拿同一把键写批注"不会被判成复用（依赖 B 的键按意图生成）；
  ⑥ 意图摘要把标题/正文都算进去，正文改一个字就是新意图——这是刻意的，但也意味着"润色同一条笔记"必须用新键。
- 相邻既有决定：[阅读停点与私人笔记契约](./2026-10-22-rc1-reading-owner-phone-reading.md)、
  [主体材料按当前版本续读](./2026-10-24-rc1-subject-materials-version-aware-reading.md)、
  [手机实例是主体 Life 的唯一 live 绑定来源](./2026-10-23-rc1-phone-life-subject-binding.md)、
  [单一写者隔离](../process/2026-10-16-single-writer-isolation.md)。
- 机制与结果：[A 侧交付结果](../../../../docs/engineering/v04-reading-response-result.md)、
  [设计进度对照](../../../../docs/engineering/v04-design-progress.md)。

## Verification

- 只跑**新增类的 exact IDs**（`$env:PI_SCRATCH_DIR\a_run_new_exact.py`，从类 `__dict__` 取 `test_*`，
  不用 `discover`、不用类继承），WSL + `PYTHONDONTWRITEBYTECODE=1`，repo cwd：
  - `tests/test_subject_reading_response.py::ReadingResponseParseTest` → 20/20 `ok`；
  - `tests/test_subject_reading_annotation_plan.py::PageAnnotationPlanTest` → 20/20 `ok`。
  `SUMMARY ids=40 failures=0`，shell 侧 `EXIT=0`，日志 `scratch/a-new-tests.log`，无 `FAILED`/`ERROR`/traceback。
- 反例都有断言：note 缺 body / title 201 / body 1201 / note 带 `note`；unclear 带 title；`no_change` 带正文、
  未知 kind、多出字段、非 JSON、空输出、模型失败（不返回替身内容）；上下文多键与空页（client 一次未调）；
  计划侧无 acquisition / 旧 revision / 撤权 / 未取得该页 / 绑错 artifact / 借来的键 / 异意图同键，全部按类拒绝；
  同意图重放仍只有一条笔记与一条归属；两个键同页是两条笔记；`no_change` 逐字节不变且无归属；
  批注前后停点与取得条目完全相等；归属记录里没有标题与正文。
- 夹具与清理：两个测试文件纯内存（无 socket、不落盘、不建临时目录、不删任何文件），因此没有需要保留的
  唯一夹具根，也没有任何 fixture 被删；未 `py_compile`、未跑旧回归、未做全树 SHA、未改配置/实例/数据库、
  未启动常驻、未取密钥、未 commit/push/tag/deploy。
- 手写 docstring 与格式门：notes 树/格式门由父级在收尾时统一跑一次（本轮不重复全门）。
- 第二轮（父级只读复核后的定点返修，只跑 4 个 exact ID，未重跑 40 全套）：修掉"按页选取得"
  （`_annotation_acquisition`：同一版先读第 1 页再读第 2 页不再 `PAGE_NOT_ACQUIRED`，部分撤权时不借被撤那条，
  该页自己的取得被撤权才报 `SOURCE_UNAVAILABLE`）与"已落 note 的键再来说 `no_change` 绕过原键检查"。
  `$env:PI_SCRATCH_DIR\c_run_exact_ids.py` 跑
  `test_a_second_page_on_the_same_revision_annotates_its_own_acquisition`、
  `test_a_revoked_page_one_never_covers_page_two`、
  `test_a_no_change_under_a_key_that_already_landed_a_note_is_refused`、
  `test_no_change_plans_nothing_at_all`（必要旧例）→ 4/4 `PASS`，`SUMMARY ids=4 failures=0`，`EXIT=0`，
  日志 `scratch/a-plan-fix.log`。
