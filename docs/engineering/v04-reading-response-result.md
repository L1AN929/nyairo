# v0.4 私人阅读 A 侧交付结果：有限内容响应 + 阅读批注纯计划

> 三层表述（不合并）：**接口与解析已验证**（注入假 client + 纯文档计划，40 个 exact IDs 全绿）；
> **真实模型未调用**（0 次 provider 请求）；**未接 runtime**（B 的核心执行与 assembly 不在本轮写集内）。
> 本轮不合格的说法：`她自己写了笔记`、`模型自主选择不记录`。

- 仓库：`D:\nyairo-rc1-body-backend`（分支 `work/rc1-phone-world-body`，HEAD `41e818f`，dirty 成果保留，未 add/commit/reset）
- 施工单：`D:\NYAIRO-v0.4-私人读写实践-施工单.md`（A 段，固定接口 + 独占写集）
- 设计依据：总体设计 16（私人实践 / 有结尾作品 / 不交账）、01 §3/§5/§8、18 §3/§7、19
- 决策记录：`.agents/notes/implemented/feature/2026-10-25-v04-reading-response-annotation-plan.md`
- 进度对照：`docs/engineering/v04-design-progress.md`

---

## 1. 产品写集（A 相关文件，未越界）

```
新增：components/life/scripts/subject_reading_response.py（378 行）
改：  components/life/scripts/private_daily_items.py（+9 常量/字段，+1 纯函数区，+__all__，原语义未改）
新增测试：tests/test_subject_reading_response.py（20 例）
         tests/test_subject_reading_annotation_plan.py（20 例）
新增文档：docs/engineering/v04-reading-response-result.md（本文件）
新增笔记：.agents/notes/implemented/feature/2026-10-25-v04-reading-response-annotation-plan.md
```

未触碰：`autonomous_life_cycle.py` / `autonomous_life_assembly.py` / `model_continuation_writer.py`（B 写集）、
`components/phone/life.py`（P 写集）、`phone_reading.py`、`life_artifact_port.py`、`one_day_life.py`、
`components/supply/**`、`components/world/**`、`web/**`、任何配置/实例/数据库。

## 2. 固定接口（B 依赖，逐字按施工单；本轮未自行改动）

```python
# components/life/scripts/subject_reading_response.py
ModelReadingResponse(client, *, max_page_chars=6000, max_note_chars=1200,
                     timeout_s=20, max_output_tokens=512)
    .respond(context, *, observed_at) -> dict
    .describe() -> dict
ReadingResponseError(RuntimeError)
class _FakeClient  # 仅测试：可注入 complete(...) 的替身
```

```python
# components/life/scripts/private_daily_items.py
plan_page_annotation(document, *, artifact_id, source_id, source_revision, page,
                     response, operation_key, now) -> dict
# -> {"content", "changed", "item_id", "kind", "operation_key"}
```

`client` 只需 `complete(system_prompt=..., user_prompt=..., timeout_s=..., max_output_tokens=...)`
返回带 `text` 的对象（与 `model_continuation_writer` 完全同形，生产 assembly 可复用同一个已配置 client/transport）。

### 2.1 `respond()` 的输入与输出

`context` 只认六个键（多一个键就多一条可能被送进模型的私人数据，一律拒绝
`READING_CONTEXT_UNKNOWN_FIELD:<key>`）：

| 键 | 要求 |
|---|---|
| `source_id` / `source_revision` | 非空文本 |
| `page` | 1 起的整数，或非空 locator 字符串 |
| `text` | 该页真实正文；全空白 → `READING_TEXT_REQUIRED`（**不调模型**） |
| `previous_page` / `total_pages` | 可缺省；给了必须是 >=1 的整数 |

返回：

```python
{"kind": "note"|"unclear"|"no_change",
 "title": str|None, "body": str|None, "note": str|None,
 "source_id": str, "source_revision": str, "page": int|str,
 "observed_at": str,
 "prompt_chars": int, "page_chars": int, "page_text_truncated": bool}
```

`no_change` 的三个文本字段恒为 `None`（不是空字符串），所以下游没有机会把它当内容。

### 2.2 三种意图与严格边界

| kind | 必需 | 禁止 | 上限 |
|---|---|---|---|
| `note` | `title` + `body`（都非空） | `note` 字段 | title <= 200，body <= `max_note_chars`(1200) |
| `unclear` | `note`（非空） | `title` / `body` | note <= 1200 |
| `no_change` | 无 | 任何非空 `title`/`body`/`note`（伪正文） | — |

* 输出对象只认 `kind`/`title`/`body`/`note`/`self_note`；别的键 → `RESPONSE_UNKNOWN_FIELD:<k>`。
  `self_note` 容忍但**丢弃**（它也可能含私人文字）。
* 超长**不截断**，直接失败（截断等于对"她到底写了什么"说谎）。
* 模型调用失败 → `MODEL_CALL_FAILED:<failure_class>`；非 JSON → `MODEL_OUTPUT_NOT_JSON`；
  空输出 → `MODEL_OUTPUT_EMPTY`；非对象 → `MODEL_OUTPUT_NOT_AN_OBJECT`。
* 没有 client → 构造即 `MODEL_CLIENT_REQUIRED`：B 应报 `NOT_CONFIGURED`，**不许**伪造替代内容。
* 正文只进这一次请求：模块不留 `raw` 输出；`describe()` 只有配置、次数、长度与错误码。

### 2.3 `plan_page_annotation()` 的语义

输入文档必须是 **`plan_page_read` 已经写好"这一页真的取得过"** 的那一版（B 的原流程不变）。计划自己做真实核实：

1. `doc_type`/`items`/`order` 形状与绑定的 `artifact_id`（`DOCUMENT_BOUND_TO_ANOTHER_ARTIFACT`）；
2. 存在**这一份 source + 这一个 revision** 的 `source_acquisition`（否则 `SOURCE_NOT_ACQUIRED:<src>@<rev>`）；
3. 用 `_annotation_acquisition` **按页**选取得（不是该 revision 的第一条）：item_type 正确、source/revision
   匹配、`available` 真为真，且它自己的 `obtained["pages"]` **真的覆盖这一页**；多条满足时取最近一条
   （文档顺序 = 写入顺序）。该页自己的取得被撤权 → `PrivateItemsSourceUnavailable`
   （`SOURCE_UNAVAILABLE:...`）；有可用取得但都不覆盖这一页 → `PAGE_NOT_ACQUIRED:<page>`；
   不猜"整版都读过"，`obtained` 形状不对也不认。

产出**最多一条**：

* `note` → `ITEM_MEMO`，带 `reading_anchor:{source_id, source_revision, page}`（与手机阅读面同一格式，
  属元数据白名单；正文仍只在 `full_text` 里），`revisions=1`；
* `unclear` → `ITEM_UNCLEAR_MARKER`（`position` = 该页，`note` = 疑问原文）；
* `no_change` → **零条目**，`content` 与输入**逐字节相同**、`changed=False`、`item_id=None`，
  也不写 `reading_operations`；停点一个字节都不动。

幂等与拒绝：

* 意图摘要 = `sha256(json([action, artifact, source, revision, page, kind, title, body, note]))`
  → `reading-intent:<32 hex>`（与手机阅读面同形，**只存哈希，不存正文**），和效果写进同一版本的
  `reading_operations`（上限仍是 200 条，旧记录照旧被挤掉）；
* 同键 + 同意图 → `changed=False` + 原 `item_id`（不新落一版、不重复一条）；
* 同键 + 任何异意图（换正文、换疑问、换 kind、换页）→ `PrivateItemsContractRefused`
  （原因串前缀 `READING_OPERATION_KEY_REUSED`）；
* `no_change` 也必须过这一关：它零写、没有效果可归属，所以只接受**全新键**；这把键已经落过
  note/unclear（哪怕内容一模一样）就按异意图拒绝；
* 借来的键（记录里没有 `intent` 摘要，例如别的 writer 落的）→ 同样 `READING_OPERATION_KEY_REUSED`；
* 条目 id 只由操作键派生（`memo:<16>` / `unclear:<16>`）：重试落在同一行；即使归属掉出 200 条窗口，
  也从条目本身核对内容，认不出就拒绝；
* 版本字段从文档自己的 `owner_version_number` +1（与 `plan_page_read` 同一规则），
  所以 B 拿到 `content` 之后照原样 `expected_head` CAS 提交即可；
* 不读 socket、不提交、不碰 `metadata_view` 之外的投影，`understanding` 恒 `UNKNOWN`。

## 3. 真实测试结果（只跑新增类的 exact IDs）

方法与运行器：`$env:PI_SCRATCH_DIR\a_run_new_exact.py`（WSL + `PYTHONDONTWRITEBYTECODE=1`，repo cwd）。
它**只**从被测类的 `__dict__` 收集 `test_*` 逐个构造用例，**不用** `discover`、
**不用** `loadTestsFromTestCase`/类继承，因此不会带出任何旧套件。

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && export PYTHONDONTWRITEBYTECODE=1 \
  && python3 /mnt/c/Users/28556/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/a_run_new_exact.py'
```

| 文件 / 类 | exact IDs | 结果 | 运行器退出码 | 原日志 |
| --- | --- | --- | --- | --- |
| `tests/test_subject_reading_response.py::ReadingResponseParseTest` | 20 | 20/20 `ok` | `0` | `scratch/a-new-tests.log` |
| `tests/test_subject_reading_annotation_plan.py::PageAnnotationPlanTest` | 20 | 20/20 `ok` | `0` | 同上 |

`SUMMARY ids=40 failures=0`，shell 侧 `EXIT=0`；无 `FAILED`/`ERROR`/traceback。

### 3.1 定点返修（父级只读复核后的第二段，只跑 4 个 exact ID）

父级只读发现两个真实缺陷，都只在 A 写集内修复（接口签名一字未改）：

1. `_available_acquisition(doc, source, revision, None)` 返回该 revision 的**第一条**取得：同一版先读第 1 页
   再读第 2 页时，第一条只覆盖第 1 页 → 第 2 页批注会 `PAGE_NOT_ACQUIRED`；且部分撤权时第一条可能就是被撤权的那条。
   现在 planner 用**自己的** `_annotation_acquisition`：item_type/source/revision 匹配、`available` 真为真、
   自己的 `obtained["pages"]` 真覆盖当前页，取最近一条满足的；一条都不满足时按原因拒绝
   （无取得 → `SOURCE_NOT_ACQUIRED`；该页自己的取得被撤权 → `SOURCE_UNAVAILABLE`；否则 `PAGE_NOT_ACQUIRED`）。
   共享 helper 未改。
2. 同键已落 note 后再来 `no_change` 绕过了 `reading_operations` 检查（违反全意图复用）。现在任何 kind 都先查原键：
   `no_change` 只接受**全新键**，已落过效果的键直接 `READING_OPERATION_KEY_REUSED`；新键 `no_change` 仍然零写。

只跑 4 个 exact ID（`$env:PI_SCRATCH_DIR\c_run_exact_ids.py`，显式 ID、断言方法定义在类自身 `__dict__`）：

| exact ID | 结果 |
| --- | --- |
| `…PageAnnotationPlanTest.test_a_second_page_on_the_same_revision_annotates_its_own_acquisition` | `PASS` |
| `…PageAnnotationPlanTest.test_a_revoked_page_one_never_covers_page_two` | `PASS` |
| `…PageAnnotationPlanTest.test_a_no_change_under_a_key_that_already_landed_a_note_is_refused` | `PASS` |
| `…PageAnnotationPlanTest.test_no_change_plans_nothing_at_all`（必要旧例，证新键 no_change 仍零写） | `PASS` |

`SUMMARY ids=4 failures=0`，shell 侧 `EXIT=0`，日志 `scratch/a-plan-fix.log`，无 `FAILED`/`ERROR`/traceback；
按父级要求**没有**重跑 40 全套。

反例在测试里都有断言（不是"看起来对"）：

* 三种意图各自的合法形状；note 缺 body、title 201 字、body 1201 字、note 带 `note` 字段、
  unclear 带 title、`no_change` 带伪正文、未知 kind、多出字段、非 JSON、空输出，全部抛错且**不返回替代内容**；
* 上下文多一个 `material_body` 键、空页 → 拒绝且 **client 一次都没被调用**；
* 计划侧：没有真实 acquisition、旧 revision、撤权 source、未取得该页、文档绑到别的 artifact、
  借来的键、异意图同键、`no_change` 带正文、未知 kind、超长，全部按类拒绝；
* 同意图同键重放 `changed=False` 且条目仍只有一条、归属仍只有一条；两个键同页 → 两条笔记；
* `no_change` 的 `content` 与输入**逐字节相同**、条目未变、没有 `reading_operations`；
* 批注前后 `reading_position` 与 `source_acquisition` 条目**完全相等**，`understanding` 仍 `UNKNOWN`；
* 归属记录里既没有标题也没有正文，只有 `reading-intent:` 摘要，且 `acquisition_id` 指向真实条目。

夹具与清理：两个测试文件都是**纯内存**，不建 socket、不落盘、不建临时目录、不删任何文件，
因此没有"唯一夹具根"需要保留（也没有任何 fixture 被删）。`PYTHONDONTWRITEBYTECODE=1`，
没有 `py_compile`，没有 `unittest discover`。

## 4. 给 B 的接线约束（只读依赖，不改 A 的签名）

1. 只有当前材料页有真实正文时调 `respond()`；**空页不要调**（会得到 `READING_TEXT_REQUIRED`），
   照常保存取得与停点。
2. `response` 原样交给 `plan_page_annotation`；`changed=True` 时把 `content` 用**调用方自己的**
   `expected_head` 提交（source/head 变化或暂停时不写，不追加盲重试、不换新 key）。
3. `no_change` 与失败必须分开：`no_change` 是成功结论（照常保存取得/停点，零批注），
   `ReadingResponseError` 是失败（不当成功、不写半条、也不伪造一条笔记）。
4. 操作键按"源/版本/page + 响应意图"生成；同一个键不要跨意图复用（会被拒绝）。
5. 私密正文不进 base 输出、`describe`、raw 日志：`ModelReadingResponse.describe()` 只给计数与错误码；
   不要把 `context["text"]`、`response["body"]`/`["note"]` 写进任何 facts/log 字段。
6. 本轮 A 的 `plan_page_annotation` 只认 `obtained["pages"]`；B 的 `plan_page_read` 调用请继续传
   `obtained={"pages": [N], "chars": L}`（与手机阅读面一致），否则计划会以 `PAGE_NOT_ACQUIRED` 失败关闭。

## 5. 遗留与已知边界

* **B/P 未实现**：`autonomous_life_cycle`/assembly/`model_continuation_writer` 的 `no_change`、
  `components/phone/life.py` 的记忆参与预检都在别的写集，本轮一行未改，也不宣称它们已接上。
* **没有真实模型证据**：全部断言基于注入替身；"她自己的笔记"仍是合成内容，不是真实模型输出。
* **没有 owner 级链路证据**：`plan_page_annotation` 是纯计划，本轮不需要也不启动 Supply owner；
  真实 owner 上"取得+停点+批注同一版本、跨进程续接"的证据要等 B 的同实例链路。
* **两份长度边界是刻意的镜像**（解析器 200/1200，PDI 200/1200）：PDI 不 import 模型侧模块，
  两份由 `test_the_reader_bounds_match_the_response_parser` 钉住相等；将来改上限必须两边一起改。
* **`no_change` 只接受全新键**：它没有效果可归属，所以不写 `reading_operations`；反过来说，一把已经落过
  note/unclear 的键再来说"不记录"会被 `READING_OPERATION_KEY_REUSED` 拒绝（那正是换意图）。
  先 `no_change`、后来再拿同一把键写批注不会被判复用（没有记录可核对），依赖 B 的键按"响应意图"生成。
* **页级取得选择是 A 自己的**：`_annotation_acquisition` 只在本 planner 内使用，共享的
  `_available_acquisition`（停点用）语义一字未改；以后谁改那条通用 helper，都不会把页级选择带偏。
* 未跑旧回归、未做全树 SHA、未改配置/实例、未启动常驻、未取密钥、未 commit/push/tag/deploy；
  第二轮返修按父级要求只跑了 4 个 exact ID，没有重跑 40 全套。
