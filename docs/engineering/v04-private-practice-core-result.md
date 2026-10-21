# v0.4 私人读写实践 B 侧交付结果：主体执行（一页 + 至多一条私密批注 + 作品 no_change）

> 三层表述（不合并）：**机制已验证**（真实 Supply owner + 真实 runtime + 真实 PDI 纯计划 +
> 明确内容替身，29 个 exact IDs 全绿）；**真实模型未调用**（0 次 provider 请求、0 密钥、0 配置）；
> **不是「她自己读懂了」**（笔记内容是测试替身给的，`understanding` 恒 `UNKNOWN`）。
> 本轮不合格的说法：`她自己写了笔记`、`模型自主决定不记录`、`真实 model 已接入验收`。

- 仓库：`D:\nyairo-rc1-body-backend`（HEAD `41e818f`，全部既有 dirty 成果原样保留；未 add/commit/reset/stash）
- 施工单：`D:\NYAIRO-v0.4-私人读写实践-施工单.md`（B 段，独占写集）
- 上游：`docs/engineering/v04-reading-response-result.md`（A 的 `subject_reading_response` +
  `private_daily_items.plan_page_annotation`，签名一字未改）
- 决策记录：`.agents/notes/implemented/feature/2026-10-25-v04-private-practice-cycle-writer-no-change.md`
- 本文件是**接替**国内 DeepSeek B 的收口（非重做）：原 B 的代码与测试全部读取保留，只修缺陷。

---

## 1. 写集（严格独占，未越界）

```
改：components/life/scripts/autonomous_life_cycle.py
    components/life/scripts/autonomous_life_assembly.py   （原 B 已写，本轮只读核对）
    components/life/scripts/model_continuation_writer.py  （原 B 已写，本轮只读核对）
改测试：tests/test_subject_reading_practice_cycle.py（原 B 文件，读后扩/修）
新增测试类内用例：无新文件（tests/test_model_continuation_no_change.py 原样通过，未改）
新增文档：docs/engineering/v04-private-practice-core-result.md（本文件）
新增笔记：.agents/notes/implemented/feature/2026-10-25-v04-private-practice-cycle-writer-no-change.md
```

未触碰：`subject_reading_response.py` / `private_daily_items.py`（A 写集）、`components/phone/**`（P 写集）、
`tests/test_subject_reading_cycle.py`（C 的夹具，只 import 复用）、任何配置/实例/数据库/socket/密钥。
`components/life/scripts/autonomous_life_assembly.py` 与 `model_continuation_writer.py` 的 dirty 内容经复核
已满足施工单（同一 client/transport/请求预算/pause admission 构造 `ModelReadingResponse`；显式
`kind=no_change`），本轮**未改一字**。

## 2. 父级指出的问题：逐条落地

| # | 问题 | 落地（`autonomous_life_cycle.py`） |
|---|---|---|
| 1 | `_reading_response_plan` 把 `ModelReadingResponse.respond` 的**整个 dict**（含 `source_id`/`source_revision`/`page`/`observed_at`/`prompt_chars`/`page_chars`/`page_text_truncated`）交给 A 的 planner，而 A 只认 `kind`/`title`/`body`/`note`（+容忍 `self_note`）→ 真实响应必被 `ANNOTATION_RESPONSE_UNKNOWN_FIELD` 拒 | 边界严格筛字段：新增 `RESPONSE_ANNOTATION_FIELDS = ("kind","title","body","note")`，只把这四个键交给 planner（`self_note` 一并丢弃，它也可能含私人文字）；源元数据**只核对不转发**（`_check_response_metadata`），`source_id`/`source_revision`/`page` 与本次请求不一致 → `ReadingResponseFailed`（错误串只带字段名，不带正文）。校验在 `no_change` 分支之前，所以三种意图都过这道门 |
| 2 | `plan_page_read` 与 `plan_page_annotation` 各 bump 一次文档内部版本，合并后是 owner+2 | 新增 `_rebase_merged_version(content, owner_version_number=…)`：合并后的字节重新盖上**owner 真正的下一个版本号**（`head_number + 1`，与 `plan_page_read` 同一规则），`owner_version_number`/`doc_revision` 同时改，其余条目/键/时间戳一字节不动。测试断言合并文档与 owner 新版本号都是 +1（`test_a_real_response_shape_reaches_the_real_planner_in_one_version`） |
| 3 | 内容 request 期间材料/PDI 变化 | 新增 `_recheck_reading_sources`：只在**真的发过私密请求**后执行，重读材料 head 与 PDI head，并对比本次 source/revision 下「原本可用、现在被撤权」的 acquisition（`_revoked_acquisitions`）。任一变化 → `ReadingResponseFailed`，**零写**（连取得都不写）。没有伪全域 ACID：两个 owner 两把 CAS，这里只做「发现变了就不写」 |
| 4 | 空白页 | 不调内容 writer（A 会以 `READING_TEXT_REQUIRED` 拒绝，且没有正文可谈），**照常保存真实取得+停点**，报告 `annotation.state = "SKIPPED_EMPTY"` |
| 5 | `no_change` 不造 owner 动作/版本/完成/经历 | 保留原 B 语义：`no_change` 只走 page read 的 CAS，零批注、零 `reading_operations`；作品 writer 的 `no_change` 在 AR-0 与直连两条路径都在 owner 提交前停（`_work_no_change` 报 `NO_ACTION` + `work_no_change{owner_called:False, version_created:False}`） |

### 2.1 本轮自己发现并修掉的两个真实缺陷

1. **`_commit_next_version` 有一个不可达的重复 `except Exception`**（原 B 的 dirty 改动引入）：
   第一个 `except` 只在 `no_change` 时 `raise`，其它异常直接掉出去 → `content` 未绑定 →
   `UnboundLocalError`，真实的 `CONTENT_FAILED` 收据永远拿不到。已合并成**一个** `except`：
   `no_change` 重抛，其它异常照旧产出 `CONTENT_FAILED` 收据（`tests/.../test_a_real_content_failure_on_the_direct_path_is_a_refused_receipt`）。
2. **步骤 operation key 不含响应意图**：原 B 只把「page slot」拼进 key，note 与 unclear 在同一个
   page 上得到**同一把** key（原 B 自己的用例因此失败）。现在 `_response_intent()` 给出
   `kind+title/body/note` 的**纯哈希**（32 hex，正文不进 key/报告/日志），拼进合成 key：
   同一意图重放同键，换意图换键，且 A 的 planner 仍按 page slot 拒绝第二意图（`READING_OPERATION_KEY_REUSED`）。

### 2.2 有意保留、不扩大重构的地方

* `is_no_change()` 的**类名兜底**保留（`type(exc).__name__ == "ModelNoChange"`）：`model_continuation_writer`
  在测试里会经多个 `sys.path` 入口被加载成两个模块对象，`isinstance` 对不上；按名兜底是那条路径的
  唯一识别手段，且名字专有、风险低。只保留现状，没有扩散到别的异常分类。
* `_reading_response_plan` 的 `None`（未配 writer）语义、`NOT_CONFIGURED` 报告、`_PauseAdmittedReadingResponse`
  的请求前准入、`pause`/`request budget` 不被吞掉：原 B 已写对，未改。

## 3. 真实测试结果（只跑新增 / 失败 / 直接受影响，exact IDs，`__dict__` 选类）

运行器：`$env:PI_SCRATCH_DIR\b_run_exact.py`（WSL + `PYTHONDONTWRITEBYTECODE=1`，repo cwd；
只从被测类的 `__dict__` 收集 `test_*`，**不用** `discover`、**不用**类继承展开）。
没有 `py_compile`，没有全树回归，没有全树 SHA。

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && export PYTHONDONTWRITEBYTECODE=1 \
  && python3 /mnt/c/Users/28556/.pi-desktop/scratch/<sid>/b_run_exact.py <module>:<Class>'
```

| 文件 / 类 | exact IDs | 结果 | 运行器退出码 | 日志 |
|---|---|---|---|---|
| `tests/test_subject_reading_practice_cycle.py::SubjectReadingPracticeCycleTest` | 14 | 14/14 `PASS` | `0` | `scratch/b-new-cycle.log` |
| `tests/test_model_continuation_no_change.py::ModelContinuationNoChangeTest` | 6 | 6/6 `PASS` | `0` | `scratch/b-new-writer.log` |
| `tests/test_subject_reading_cycle.py::SubjectReadingCycleTest`（直接受影响：无 writer 的旧路径） | 9 | 9/9 `PASS` | `0` | `scratch/b-affected-reading.log` |

`SUMMARY ids=14/6/9 failures=0`，三次 shell 侧 `EXIT=0`，无 `FAILED`/`ERROR`/traceback。

### 3.1 本轮新增/改写的 6 个用例（都在 B 自己的测试类内，父类只当夹具）

1. `test_a_real_response_shape_reaches_the_real_planner_in_one_version` —— 替身按 A 的**真实形状**
   回传（带 7 个元数据键），planner 用 A 的真实 `plan_page_annotation`（无 mock）：一条 memo 落地、
   `reading_operations` 一条、文档 `owner_version_number == owner_head+1`（**不是 +2**）、
   owner 新版本号 +1、报告里没有笔记正文。
2. `test_a_response_that_contradicts_its_page_is_refused_and_writes_nothing` —— 元数据说别的
   source/revision/page → `STEP_REFUSED`，PDI head 不动、`items` 仍空。
3. `test_a_blank_page_keeps_the_real_stop_and_skips_the_content_writer` —— 第 1 页空白：
   `SKIPPED_EMPTY`、writer 一次都没被调、停点 `[1]` 真实落盘、零批注。
4. `test_a_material_change_during_the_request_writes_nothing` —— 请求期间材料换版 →
   `STEP_REFUSED`（`material changed during the private response`），零写。
5. `test_a_revocation_during_the_request_writes_nothing` —— 请求期间真实 owner
   `mark_source_unavailable` → `STEP_REFUSED`（`revoked during the private response`），
   条目集合不变、停点仍 `[1]`。
6. `test_the_work_writer_no_change_stops_before_the_owner_on_the_direct_path` /
   `test_a_real_content_failure_on_the_direct_path_is_a_refused_receipt` —— 直连分支的两半：
   `no_change` 抛 `ModelNoChange` 且无收据/无版本，报告半边是 `NO_ACTION`；真实
   `ModelContentError` 得到 `CONTENT_FAILED`/`REFUSED` 收据（不再 `UnboundLocalError`）。

原 B 的其余 8 个用例全部保留并通过（`NOT_CONFIGURED` 旧行为、note/unclear/no_change、
响应失败零写、暂停不问、AR-0 路径 no_change）。其中一处原 B 断言本身写错：它断言第 1 页的
请求里含**第 2 页**的特征串（`MATERIAL_SENTINEL`）；已改成断言第 1 页自己的特征串在请求里、
第 2 页的不在，并继续断言两者都不进报告。

夹具与清理：所有用例都在 `OneDayTestCase` 的真实隔离实例（Supply owner + socket + PDI 文档）上跑，
**没有任何 fixture 被删除**，没有 `TemporaryDirectory` 清理、没有 no-op `unlink`、没有物理删除。
`PYTHONDONTWRITEBYTECODE=1` 全程开启；仓库里唯一的 `__pycache__`
（`components/life/scripts/__pycache__`，10-18 18:25）是本轮之前就存在的，未动。

## 4. 给 A / 父级的接口事实（只读依赖）

* A 的签名**一字未改**，B 只按 `plan_page_annotation(document, *, artifact_id, source_id,
  source_revision, page, response, operation_key, now)` 调用；`response` 现在**只含四个语义键**。
* `respond()` 的元数据不再转发，但被核对：将来 A 若改 `page` 的类型（int ↔ locator 字符串），
  B 的比较是 `str()` 相等，仍然成立；若 A 增删返回键，B 不关心（多余键忽略、缺失键跳过核对）。
* B 提交的字节是「A 的合并结果 + 版本号回盖」，A 的 planner 语义不受影响；`reading_operations`
  里仍是 A 写的 `reading-intent:<32hex>`。
* 步骤 key = `operation_key_for(plan_page_read 的 key, "page_annotation", page slot key, response intent digest)`。

## 5. 局限与已知边界（不粉饰）

1. **0 真实模型**：所有答复都是注入替身；`ModelReadingResponse` 的真实 provider 一次都没被调用，
   「她真的写了这句」没有任何证据。装配层（`_model_reading_response`）只是被读到，未被真实配置驱动。
2. **直连 port 路径的整轮链路不可达**：探针（`scratch/b_probe_direct.py`）显示，不给 AR-0 适配器时
   production runtime 根本不把「续写作品」候选提供给决策（`cognition.offered` 只有两个
   `PERSONAL_OPPORTUNITY`），`evaluate_once` 走不到直连分支。本轮因此**直接驱动该分支的两个半边**
   （`_commit_next_version` + `_work_no_change`）证明语义，没有假装整轮跑通。这是既有 wiring 边界，
   不在 B 写集内。
3. **单进程、单实例**：「跨进程续接」由 C 的既有用例与父级验收链覆盖，B 本轮没有新增跨进程证据。
4. **没有真实 Memory 参与**：批注只在 PDI 文档里，不写 Memory/experience（本轮也不改 P 的 preflight）。
5. **未跑旧回归/全树**：只跑上述 29 个 exact IDs；notes 结构门与 `git diff --check` 由父级最终一次执行
   （本轮只在改过的文件上跑过 `git diff --check`，无输出）。
6. 未 commit/push/tag/package/deploy，未改配置/实例/数据库，未启常驻，未取密钥。

## 6. 停用与兼容退出

正常停止本批runtime并释放lease，保留同一实例已经落地的PDI/Activity/Action历史；不以旧备份覆盖后来状态。需暂不使用新读后响应时，可在受控装配中不注入可选writer，沿用已验证的NOT_CONFIGURED路径；不通过回退到Git HEAD抹掉同文件内先前已接受的0.4改动。

禁止对共享dirty文件使用整文件checkout/reset。确有代码回退需要时，先核对精确本批差异与后续兼容性，再由单一writer定点修改；PI编辑快照仅供比对，不是覆盖后来历史的授权。本轮未执行任何回退。
