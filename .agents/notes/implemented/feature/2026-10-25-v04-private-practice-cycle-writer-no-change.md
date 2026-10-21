# Agent Note: v0.4 私人读写实践的主体执行（一页取得 + 至多一条私密批注 + 作品 no_change）

Status: implemented

## Problem

0.4 的产品效果是：主体在**已有的真实阅读活动**里实际取得一页，有限内容模型可以留一条私人笔记、一个疑问，
或者什么都不记；取得、停点与这条可选批注落在**同一个 PDI 版本**里，下一轮读视图能看到真实批注计数与锚点，
跨进程继续原活动；作品 writer 可以明确 `no_change`，不因为「必须产出」而强制加字。A 侧（
`subject_reading_response.ModelReadingResponse` + `private_daily_items.plan_page_annotation`）已交付，
B 侧执行层在接线时暴露出六个真实问题：

1. **真实答复会被自己的计划拒掉。** `_reading_response_plan` 把 `respond()` 返回的**整个 dict** 交给 A 的
   纯计划，而 A 只认 `kind`/`title`/`body`/`note`（另容忍并丢弃 `self_note`）。真实响应还带
   `source_id`/`source_revision`/`page`/`observed_at`/`prompt_chars`/`page_chars`/`page_text_truncated`，
   于是任何真实答复都会被 `ANNOTATION_RESPONSE_UNKNOWN_FIELD` 拒绝 —— 用替身喂一个"干净"的 dict 才测得通，
   等于测的不是生产形状。
2. **版本号会撒谎。** `plan_page_read` 把文档盖成 owner 的下一个版本号（+1），`plan_page_annotation`
   又把它手上的文档再 +1。两步都基于同一个基线，合并后是 owner+2，而 owner 真实只会创建 +1：提交这些
   字节等于让文档对自己的版本号说谎。
3. **私密请求是一个模型等待（最多 `timeout_s`），期间两个 owner 都可能动。** 材料和 PDI 文档是两个不同
   owner、两把 CAS，没有跨它们的事务。原实现只靠"提交时的 `expected_head` 大概会失败"，既没重读源 head，
   也没看这一页的取得是否在请求期间被撤权。
4. **空白页会把内容请求打成一个失败。** A 对空正文直接 `READING_TEXT_REQUIRED`，于是"她走到一个空白页"
   会被报成响应失败，连真实停点也一起丢掉。
5. **作品 `no_change` 的直连路径是坏的。** `_commit_next_version` 里有两个 `except Exception`：第一个只在
   `no_change` 时 `raise`，其它异常掉出去后 `content` 未绑定，真实的内容失败会变成 `UnboundLocalError`
   而不是 `CONTENT_FAILED` 收据。
6. **步骤 key 不含响应意图。** 合成 key 只拼了 page slot，note 与 unclear 在同一页得到同一把 key，
   "一个意图一个逻辑动作"在 action 层不成立（A 的 planner 仍会拒第二意图，但 key 本身说不清）。

## Decision

只改 B 的独占写集（`autonomous_life_cycle.py`），A 的签名与文件一字未动：

1. **边界筛字段 + 元数据核对。** 新增 `RESPONSE_ANNOTATION_FIELDS = ("kind","title","body","note")`，
   只把这四个键交给 `plan_page_annotation`；`self_note` 丢弃（它也可能含私人文字）。`respond()` 的源元数据
   **不转发**，改为 `_check_response_metadata` 核对：`source_id`/`source_revision`/`page` 与本次请求不一致
   就 `ReadingResponseFailed`，错误串只带字段名。核对在 `no_change` 分支之前，三种意图都过这道门。
2. **版本号回盖。** `_rebase_merged_version(content, owner_version_number=head_number+1)`：只重写
   `owner_version_number`/`doc_revision`，条目、键、时间戳一字节不动。合并后的字节因此写着 owner 真正会
   创建的那一个版本号。
3. **请求后重读两个 owner，变了就不写。** `_recheck_reading_sources` 只在**真的发过私密请求**后执行：
   重读材料 head（换版 → 拒）、重读 PDI 文档（先比对本次 source/revision 下"原本可用、现在被撤权"的
   acquisition，再比 head 是否移动），任一变化 → `ReadingResponseFailed`、零写（连取得都不写）。不假装
   全域 ACID：只做"发现变了就不写"。撤权检查放在 head 检查之前，因为撤权本身就是一次写。
4. **空白页跳过内容请求但保留真实停点。** 空正文不调 writer（没有正文可谈，A 也会拒），照常提交取得+停点，
   报告 `annotation.state = "SKIPPED_EMPTY"`。
5. **修掉重复 `except`。** 合并成一个：`no_change` 重抛（`evaluate_once` 报 `NO_ACTION`），其它异常照旧产出
   `CONTENT_FAILED`/`REFUSED` 收据。
6. **key 含意图摘要。** `_response_intent()` 给出 `kind+title/body/note` 的 sha256 前 32 hex（正文不进
   key/报告/日志），拼进合成 key：同意图重放同键、换意图换键；A 的 planner 仍按 page slot 拒绝第二意图
   （`READING_OPERATION_KEY_REUSED`），所以"变意图"照样拒绝。
7. **`no_change` 语义保持诚实。** 阅读 `no_change`：只走 page read 的 CAS，零批注、零 `reading_operations`；
   作品 `no_change`：AR-0 与直连两条路径都在 owner 提交前停，无版本/进度/经历、不自动完成 Activity，
   `_work_no_change` 报 `NO_ACTION` + `work_no_change{owner_called:False, version_created:False}`。
8. **`is_no_change()` 的类名兜底保留**（`type(exc).__name__ == "ModelNoChange"`）：`model_continuation_writer`
   在测试里会经多个 `sys.path` 入口被加载成两个模块对象，`isinstance` 对不上；按名兜底是那条路径唯一的
   识别手段。只保留现状，不扩散到别的异常分类。

## Alternatives considered

* **改 A 的 `_clean_annotation_response` 去容忍元数据键。** 最强立论：A 的 planner 是唯一知道"批注需要
  哪些字段"的地方，让它自己挑比 B 猜更集中。否决原因：A 的契约是"只认语义字段、多一个键就拒"，
  这正是隐私边界的实现；放宽它等于把 `prompt_chars` 之类请求元数据也变成"合法批注输入"，而且会让
  A/B 的写集互相越界。B 在边界筛字段成本更低、语义更准。
* **让 `plan_page_annotation` 接受"基线版本号"参数、自己只 +1 一次。** 最强立论：一次意图一个版本号本该由
  计划自己保证，回盖是调用方在补锅。否决原因：那是改 A 的签名，父级已定"A 签名不变"；而且回盖只动两个
  字段，A 的 planner 语义完全不变，回滚面更小。
* **在内容请求期间持有 pause/共享锁，做成"看起来的原子"。** 最强立论：锁住就没有并发窗口。否决原因：
  模型等待最长 20s，持锁就是伪全域 ACID —— 既锁不住另一个 owner（Supply），又会把操作员挡在外面。
  正确做法是请求后重读 + 变了不写。
* **空白页也调一次 writer，让模型自己说 no_change。** 最强立论：三种意图就都能从模型来。否决原因：
  A 对空正文 `READING_TEXT_REQUIRED` 直接拒，而且"空页"不是"没东西可写"，是"这一页没有正文"——
  报 `SKIPPED_EMPTY` 比伪造一次请求诚实。
* **把 `no_change` 实现成"提交一份同内容的版本"。** 最强立论：调用方代码最简单，一条路走到底。否决原因：
  那会凭空造一个版本和一次 owner 动作，正是 0.4 明确禁止的伪完成；`ModelNoChange` 作为非
  `ModelContentError` 的专用信号 + 提交前 `raise` 才是诚实的形状。
* **key 不含意图，靠 A 的 planner 拒第二意图就够了。** 最强立论：拒绝已经发生，key 只是标签。否决原因：
  合成 key 是 AR-0 的幂等键，两个不同意图共用一把 key 会让"同键重放"和"换意图"在 action 层无法区分；
  加一个 32 hex 摘要成本极低，且不含正文。

## Consequences

* 真实 `ModelReadingResponse` 的答复现在能真的落到 PDI 文档里：B 不再要求上游先"洗干净"响应。
* 合并版本号与 owner 版本号一致（+1），下一轮读视图/续接不会读到"超前"的版本号。
* 内容请求期间的任何材料换版、PDI 移动或取得撤权都变成"这一轮什么都没写"，而不是半条链或一条
  钉错字节的批注。
* 空白页不再被报成响应失败，停点仍然真实；报告里因此多一种批注状态 `SKIPPED_EMPTY`
  （其余是 `NOT_CONFIGURED` / `APPLIED` / `UNCHANGED` / `NO_CHANGE`）。
* 真实内容失败重新拿到 `CONTENT_FAILED` 收据（直连路径），不再 `UnboundLocalError`。
* 同页换意图在 action 层就是另一把 key，在文档层被 A 拒绝 —— 两层都拒绝，不靠单点。
* 代价：一次私密请求后多两次 owner 读（材料 head + PDI 文档）；只在真的发过请求时发生，
  `NOT_CONFIGURED` 与空白页不多读。
* 仍未接真实模型：装配层的 `_model_reading_response` 只用同一个 client/transport/预算/pause admission
  构造 `ModelReadingResponse`，本批 0 次真实 provider 调用。

## Verification

* `tests/test_subject_reading_practice_cycle.py::SubjectReadingPracticeCycleTest` 14 个 exact IDs 全 `PASS`
  （`__dict__` 选类，不 discover、不展开父类），日志 `scratch/b-new-cycle.log`，`EXIT=0`。
  其中 6 个是本轮新增/改写：真实响应形状 + 真实 planner + 版本只 +1；元数据矛盾零写；空白页
  `SKIPPED_EMPTY` + 停点保留；请求期间材料换版零写；请求期间真实 `mark_source_unavailable` 零写；
  直连路径 no_change 两半 + 真实内容失败收据。
* `tests/test_model_continuation_no_change.py::ModelContinuationNoChangeTest` 6 个 exact IDs 全 `PASS`，
  日志 `scratch/b-new-writer.log`，`EXIT=0`。
* `tests/test_subject_reading_cycle.py::SubjectReadingCycleTest`（直接受影响的旧路径，无 writer）9 个
  exact IDs 全 `PASS`，日志 `scratch/b-affected-reading.log`，`EXIT=0`。
* 探针 `scratch/b_probe_direct.py` 记录了一条边界事实：没有 AR-0 适配器时 production runtime 不把
  "续写作品"候选提供给决策，`evaluate_once` 走不到直连分支（因此该分支按两个半边驱动验证）。
* 未跑旧回归/全树、未 `py_compile`、未做全树 SHA；`PYTHONDONTWRITEBYTECODE=1` 全程开启；没有删除任何
  fixture（无 `TemporaryDirectory` 清理、无 no-op `unlink`）；未改配置/实例/数据库，未 commit/push/deploy。
