# v0.4 旧作重访与有来历的续改 · 施工报告

Status: 机制 / 真实 owner / 手机 HTTP / 同实例重开 = 已实现并实测通过；**自然模型验收未开**（本段禁止产品 completion，真实模型 calls = 0）。

## 1. 起点核实（真实源码，不是推测）

| 事实 | 证据 |
| --- | --- |
| Supply 层本来就有不可变版本行：`artifact_versions` 上有 BEFORE UPDATE/DELETE 触发器 | `components/supply/lifesupply/db.py:343-357`，`artifact/store.py:137-151` |
| store 早就有 `read_version(version_id)`（含 `a.lifecycle!='TOMBSTONED'`） | `artifact/store.py:274-280`（改前） |
| 但**协议层没有**任何"列版本/按名读版本"的 op：`READ_OPS` 只有 list_artifacts/read_artifact/read_head/lookup_operation/read_effect_receipt… | `service/life_supply.py:223-228`（改前） |
| 所以 `LifeArtifactPort` 只能 head-only，`PrivateProjectService` 的历史只能 `complete=False + PORT_LACKS_LIST_VERSIONS` | `life_artifact_port.py:46-48`（改前），`private_project_service.py:36-47`（改前说明段）、`:540-597`（改前 `_history`） |
| 这正是 2026-10-27 笔记里那条"要列全需在 port 上接 list_versions"的未闭合项；`store.read_version 已存在` **不等于**协议已支持 | `.agents/notes/implemented/feature/2026-10-27-v04-private-projects-owner-and-phone.md` §Consequences |

结论：必须**扩原 owner 的读协议**（不是新库、不是直读 SQLite），再把 Life 端口/项目服务/手机接上。没有第二作品库、没有 Memory/wallet/Activity 新 owner、没有全局人格或成长分。

## 2. 实际写集（最小）

| 文件 | 改变（行级） |
| --- | --- |
| `components/supply/lifesupply/artifact/store.py` | 新增 `DEFAULT_VERSION_PAGE/MAX_VERSION_PAGE` 与 `list_versions(artifact_id, *, limit, before_version_number)`：只回元数据（版本号/父版本/存库 `content_sha256`/字符数/时间/`operation_key`）+ `total_versions` + `next_before_version_number`。`None` = 读不到，永远不等于"没有版本" |
| `components/supply/lifesupply/service/life_supply.py` | `READ_OPS += {list_versions, read_version}`；`FIELD_TYPES`/`REQUIRED_FIELDS` 两条；`DEFAULT_VERSION_PAGE=20 / MAX_VERSION_PAGE=50 / MAX_VERSION_CONTENT_CHARS=20000`；`_history_subject_artifact()`（归属 + tombstone 扣留 + lifecycle 回传）、`_op_list_versions`、`_op_read_version`（版本必须属于该 artifact，否则 `VERSION_NOT_IN_ARTIFACT`；`max_chars` 越界 `MAX_CHARS_OUT_OF_RANGE`；截断时仍回**全量** `content_sha256`） |
| `components/life/scripts/life_artifact_port.py` | `READ_OPS` 加两 op；`list_versions()`（归一化 entries + `complete/has_more/next_before_version_number/reason_code`）、`read_version()`（纯读；`content_truncated` 诚实）；常量导出 |
| `components/life/scripts/private_project_service.py` | `read_project_version()` / `_read_version_raw()`（当前 lifecycle 与归属再过一遍，ARCHIVED 可读但 `continuable=False`）、`compare_project_versions()`（owner `content_sha256` 为判定基准 → 同正文不同版本 = `machine_head_only`；有界切片 + 首个差异窗口 + 行增删）、`read_project_provenance()`（从**第 1 版**读，版本不可变所以续写也洗不掉来源）、`create_derived_project()`（新 artifact + 首版带结构化来源行）、`_history()` 支持 dict 分页答案、`read_project_history(before_version_number=)`、`read_project()` 剥掉机器来源行并给出 `provenance` |
| `components/phone/service.py` | `project_versions / project_version / project_compare / project_provenance / create_derived_project`；常量 `MAX_PROJECT_HISTORY_LIMIT=50 / MAX_SOURCE_NOTE=120 / PROJECT_SOURCE_PURPOSES` |
| `components/phone/server.py` | GET `.../versions`（`?limit&before`）、`.../versions/<vid>`、`.../versions/<vid>/compare`、`.../provenance`；POST `/projects/derived`；`_version_path_id`、`PROJECT_DERIVE_FIELDS`、`PROJECT_VERSIONS_QUERY_FIELDS`；全部走既有 `_guard_common` / `_guard_mutation`（Host/Origin/Sec-Fetch/CSRF）、`_read_body` 白名单、`record_operation` |
| `components/phone/web/app.js` | **未改**（见 §7 未完成项） |
| `tests/test_v04_work_revisit_history.py` | 新增，12 tests，真实 Supply owner + 真实 phone HTTP（POSIX/AF_UNIX） |

没有改：`MANIFEST.json`、`scripts/fix_platform_world_move.py`、`scripts/fix_weixin_world_move.py`、`components/life/scripts/autonomous_life_*`、AG0/AG1/AG2、world 组件、任何原实例或全局配置。

## 3. 来源语义（复用既有契约，最小方案）

新作品 = **新 artifact，单 parent（它自己的第 1 版）**，不是 fork、不是 restore、不是多 parent 合并。来源记录复用仓库已有的"首行 HTML 注释标记"约定（`nyairo-material:v1` / `nyairo-research:v1`，见 `research_workspace_service.py:86-96`），新标记 `nyairo-work-source:v1`：

```
<!-- nyairo-work-source:v1 {"note":"…","purpose":"adapt","source_artifact_id":"artifact:…","source_content_sha256":"…","source_version_id":"artifact-version:…","source_version_number":1,"taken_at":"…"} -->
<她的新正文>
```

- 只由服务机器写入；单物理行、≤800 字符，正文越界即拒。
- `purpose` 只有 `quote / adapt / reuse`（引用 / 改编 / 直接复用）。
- 来源在写之前**当场重读**：源 artifact 归属、未 tombstone、版本属于该 artifact、`content_truncated=false`；任何一条不成立 → 具名拒绝 `SOURCE_NOT_READABLE` + `source_reason_code`（如 `VERSION_NOT_IN_ARTIFACT`），不写引用假证。
- 读来源永远读**第 1 版**（不可变）；后续续写不必重复标记，也不会把来源改掉。
- 源头的 head/revision/版本数在派生之后完全不变（测试断言）。
- 资料（reading material）不是项目但**可以**作为来源；`excluded_artifact_ids` 只影响"它算不算项目"。

## 4. 权限/拒绝/并发/UNKNOWN 反例（全部真实 owner）

| 反例 | 结果 | 测试 |
| --- | --- | --- |
| 别的 artifact 的版本号 | owner `NOT_FOUND/VERSION_NOT_IN_ARTIFACT`；port 抛 `ArtifactOwnerRefused`；手机派生 → 404 `SOURCE_NOT_READABLE` + `data.source_reason_code=VERSION_NOT_IN_ARTIFACT` | `test_owner_refuses_a_version_of_another_artifact`、`test_derived_post_…` |
| 别的 subject 读历史 | `DENY`/`NOT_FOUND`（`SUBJECT_NOT_OWNED` / `ARTIFACT_NOT_FOUND`） | `test_owner_enforces_its_own_bounds` |
| tombstone 后读历史 | `NOT_FOUND/ARTIFACT_TOMBSTONED`（历史一并扣留，不因"历史"例外） | `test_owner_withholds_history_of_a_tombstoned_artifact` |
| 资料当项目 | `404 PROJECT_ARTIFACT_EXCLUDED`（列表/详情/版本三面一致） | `test_reading_material_is_not_a_project_but_is_a_legal_source` |
| 分页/正文越界 | owner 自己拒：`LIMIT_OUT_OF_RANGE` / `CURSOR_INVALID` / `MAX_CHARS_OUT_OF_RANGE` | `test_owner_enforces_its_own_bounds`、`test_phone_versions_are_paged_by_the_cursor` |
| 并发 head 前移 | owner `CONFLICT`（detail `stale expected_head`），版本数不变 | `test_commit_keeps_cas_and_a_lost_answer_creates_no_second_version` |
| 答复丢了的 UNKNOWN | 同一 `operation_key` 先对账再报：`effect_state=UNKNOWN`、`reconciled=true`，版本数只 +1（无重复版本） | 同上（`_InjectedLostAnswer` 在 `LifeArtifactPort` 类上注入） |
| 手机写守卫 | 无 CSRF → 403 `CSRF_TOKEN_MISSING`；`workspace_id` → 409 `CLIENT_IDENTITY_REFUSED`；未知字段 → 400 `UNKNOWN_FIELD:*`；`subject_id` → 400 `UNKNOWN_FIELD:subject_id`；坏 purpose → 400 `SOURCE_PURPOSE_INVALID` | `test_derived_post_refuses_…`、`test_anonymous_identity_fields_are_refused` |
| GET 纯读 | 一次读遍 list/named version/compare/provenance 前后：head version id、`revision`、`total_versions`、`list_unresolved_operations` 计数完全一致 | `test_phone_reads_history_compare_and_provenance_without_writing` |

## 5. 历史读的诚实边界

- 列表**只回元数据**（无正文），`MAX_VERSION_PAGE=50`，游标 `before_version_number` 严格小于才取，翻页不重不漏。
- `complete=True` 只在"这一页就是全部历史"时为真；带游标的页为 `complete=False` 且 `reason_code=HISTORY_PAGED`，首页被截断是 `HISTORY_PAGE_TRUNCATED`，与 owner 计数不一致是 `HISTORY_COUNT_MISMATCH`。手机界面据此可如实显示"更早的版本还有"。
- 具名正文默认 ≤20000 字符；超界回前缀 + `content_truncated=true` + **全量** `content_sha256`，调用方无法把前缀说成全文。
- 比较：两侧都有 owner `content_sha256` 时以 hash 为准（`comparison.basis=owner_sha256`）；无 hash 且有界切片被切 → `content_changed=None` + `reason_code=COMPARISON_INCOMPLETE`，不猜。
- 历史读**不写** head/operation/Activity，不恢复旧版本，不把旧版本变回当前。

## 6. 实际命令与退出码（真实 subprocess returncode）

| 命令 | 结果 |
| --- | --- |
| `wsl python3 tests/test_v04_work_revisit_history.py`（`NYAIRO_WORK_REVISIT_ROOT=/tmp/wrf*`，`PYTHONDONTWRITEBYTECODE=1`） | `Ran 12 tests … OK`，EXIT=0（连续 3 次不同夹具根复跑均绿） |
| `wsl python3 -m unittest tests.test_v04_shared_practice_life_chain tests.test_v04_shared_practice_phone tests.test_v04_shared_practice_service tests.test_phone_reading_owner tests.test_phone_practice_api tests.test_phone_research_api tests.test_artifact_closeout_v04` | `Ran 140 tests … OK`，EXIT=0 |
| `wsl python3 -m unittest lifesupply.tests.test_service_socket lifesupply.tests.test_service_identity lifesupply.tests.test_idempotency lifesupply.tests.test_service_states`（cwd=`components/supply`） | `Ran 31 tests … OK`，EXIT=0 |
| `py -3.13 -B -X utf8 -m unittest tests.test_phone_projects_api tests.test_v04_private_projects_stage` | `Ran 22 tests`，21 绿 / 1 红（见 §8 既有红） |
| AST 检查（`ast.parse`，不生成 pyc）对 `store.py / life_supply.py / life_artifact_port.py / private_project_service.py / phone/service.py / phone/server.py` | `AST_OK`，EXIT=0 |

夹具：`/tmp/wrf2…/work-revisit-<pid>-<ns>` 唯一创建、**全部保留**；每个测试只 `shutdown/server_close/service.close` 自有资源，不 kill 用户进程、不删目录。

## 7. 未完成 / 明确不做（不冒充）

1. **手机界面（`components/phone/web/app.js`）未接新路由**：后端四条 GET + 一条 POST 已可用并有 HTTP 证据，但界面上的"版本列表 → 点开某一版 → 看差异 → 以这一版为来源另开一件"这一屏**没有写**。原因：本会话写入预算耗尽；不把后端 HTTP 测试当作浏览器实测。
2. **浏览器实测（Edge/CDP）未跑**：没有 DOM 交互截图/点击证据，因此本条按"未实测"计，不声明任何界面绿。
3. **正常 Life 循环里的旧正文进入真实写作上下文（sentinel 进 actual model writing context）未实现**：候选/AG1/AG2/写作 seam 未被改，`artifact_continuation_source` 未接历史片段；因此"她从旧稿里读到一句并带进新一段"的闭环**只是机制侧可讲，证据侧没有**。这是下一段第一优先级。
4. 旧 Decision 不重放 / PAUSED 占位 / fresh RESUME / quiet 零调用等语义**未被本段触碰**（没有改动主循环），所以既没回退也没新证据。
5. 真实产品模型 calls **0**：本段没发任何 `127.0.0.1:3425/v1/workbuddy-wg/cn:deepseek-v4.1-flash` 请求；固定路由与新一轮共同额度留待父级核验后另开。

## 8. 已有红（非本段引入，保留不修）

- `tests/test_v04_private_projects_stage.py:472` `test_gateway_creates_one_project_through_the_owner`：断言 `detail["artifact_id"] == "artifact:" + artifact_id`，而 `life_project_options._artifact_id_of()` 按自身文档"verbatim"回 owner 的完整 id（`artifact:<hex>`），两边相加得到 `artifact:artifact:…`。失败在本段改前改后都成立：该断言只依赖 `read_project` 的 `artifact_id`（未改）与 gateway（未改文件），FakePort `_id("artifact:")` 也只拼一层前缀。属既有测试期望陈旧，按"不大修无关领域"保留并记录，不声称全绿。

## 9. 与旧阶段的边界

前阶段"自然共作未通过"（最新 concise 有效 CONTINUE 但合法不接受）**未被覆盖**；三个旧 restart 红例与"无等价 baseline"的限制一仍其旧。本报告不把机制绿读成能力形成。

## 10. 父级 bounded review（A–F）修订与本轮实测范围

本节修订前文，凡与前文冲突以本节为准。前文 §1 的“已实现并实测通过”只覆盖**下列已证范围**：
owner 两条历史读、Life 端口/项目服务的历史读与比较、派生新作与来源标记、手机四条 GET 与一条 POST、
同实例新 runtime 读回；不覆盖手机界面渲染、真实浏览器点击、正常 Life 循环的写作上下文。

### A 当前来源资格覆盖所有派生文本（已修，已测）

`PrivateProjectService._source_chain` 从**第 1 版**（不可变）读来源标记，逐跳用 owner 自己判当前可读性
（归属/未 tombstone），深度上限 `MAX_SOURCE_DEPTH=3`，重复 id 判 `SOURCE_CYCLE`；首版读不到时
fail-closed（`unverified_first_version` → `SOURCE_ID_MISSING`），不当作“无来源”。`_source_gate` 的结论
同时用于 `read_project`、`read_project_version`、`compare_project_versions`、`read_project_provenance`：
源不可读时正文、比较出的差异、来源 note 全部扣留（`derived_text_withheld` / `content_withheld`），
只留 id、版本号、字符数、hash 等元数据；`private_text_included` 在手机门面同步为 False。
测试：`test_blocked_source_withholds_every_derived_surface`（源→派生→再派生，tombstone 根后三面都扣留、
sentinel 在完整 JSON 里不出现、派生侧版本数不变即零写）、`test_marker_removed_from_the_head_still_traces_the_first_version`
（head 把标记整行删掉后来源仍从首版读回）。depth/cycle 复用 research/practice/sharing 的“只读重算、不建第二账本”做法。

### B 一次意图只创建一次（已修，已测）

- 默认 key 由**完整意图**派生：source_artifact_id / source_version_id / title / body / purpose / note；
- 内容里的 `taken_at` 改成**源版本的 owner 记录时间**，不再读墙上时钟 —— 同一意图重放产生的 content 字节相同，
  owner 的 request digest 才不会因跨秒变化；
- 显式 key 先用 `lookup_operation` 对账：同 key 同意图 → `already_applied`；同 key 异 purpose/note/来源 →
  `OPERATION_KEY_REUSED_WITH_DIFFERENT_INTENT`（409），绝不把旧成功当成新 provenance 的答复；
- UNKNOWN 仍走既有 `_settle` → `resolve_operation` 同 key 对账，不盲发。

测试：`test_derived_replay_is_one_artifact_across_a_new_runtime_and_any_clock`（把 `pps.datetime` 换成
“调用即断言失败”的替身仍成功、新 PhoneService 新 runtime 同 key 仍同一件、project 计数只 +1、异 purpose 409）。

### C 当前读权限模型（如实报告，未自造 grant 要求）

实测原 owner 的读路径：`dispatch → _authorize(OP_CALLERS) → handler`。读 op 只要求调用者是
host-operator 或 service-principal（`life_supply.py` 的 `OP_CALLERS`），随后每个 handler 自己判
**subject 归属**（`subject_identity` + `get_artifact_for_subject`）与**lifecycle**（tombstone 扣留）。
**读 op 不查 grant、不查 workspace lifecycle**；grant 只在写 op 的 `_reserve` 里消耗。因此本轮新增的
`list_versions`/`read_version` 与既有 `read_artifact`/`read_head` 采用**完全相同**的当前权利模型，
没有多加也没有少加；报告不再声称“已验当前 grant”，因为读路径本就不存在这一检查。
源读→commit 之间的并发资格变化：owner 没有“读+写”复合边界，所以本轮做的是 **commit 后的再验**
（`create_derived_project` 写成功后重新走 `_source_gate`，源已不可读则回 `source_blocked` 并扣留正文），
加读时再验（A）。**未扩原 owner 复合边界**，这一点是未覆盖项，不是已绿项。

### D 比较真的分开“机器头”和“正文”（已修，已测）

两侧都先剥掉本模块的合法来源标记：`body_changed` 由剥离后的正文比出（有界切片，切了就 `None` +
`COMPARISON_INCOMPLETE`），`hash_changed` 由 owner 存库 hash 比出，`headers_differ` 只比 marker 的
身份字段（来源 id/版本/purpose/note）。`machine_head_only` 仅在 **hash 未变且正文未变** 时为真。
反例测试：`test_compare_separates_header_changes_from_body_changes` 覆盖“headers 不同 / body 相同”
（`headers_differ=True, body_changed=False, hash_changed=True, machine_head_only=False`）与
“headers 相同 / body 不同”（`body_changed=True, headers_differ=False`）。

### E 既有红（更正措辞）

本轮 `py -3.13 -m unittest tests.test_phone_projects_api tests.test_v04_private_projects_stage` 是
**22 项中 1 红**；该红在 `tests/test_v04_private_projects_stage.py:472`，**未做等价 baseline 归因**，
只根据“涉及文件本段未改、断言与 `life_project_options._artifact_id_of()` 自述的 verbatim 语义矛盾”
判断非本段引入。不能因此称全绿，也不修这个无关 prefix 断言。

### F 首行标记不是来源认证（如实声明，已测）

`nyairo-work-source:v1` 写在**可写正文**里，普通 `create`/`commit` 也能写进去，因此：
`provenance["verified"]` 恒为 `False`，`verification` 为 `SELF_DECLARED_FIRST_VERSION_MARKER`；
它只表示“首版里这样声称”，**不授权任何读**，也不驱动自动复用（Life 侧自动复用必须以读到源当前可读为准）。
`source_id`/`source_content_sha256` 不能单独换来访问权：claimed 但不可读的 id 会让整个派生面扣留
（测试里伪造一个 `artifact:fff...` 的来源即得 `ARTIFACT_NOT_FOUND` + 扣留）。未新增 owner 字段、
未迁移 schema、未建立第二 ledger；若要真正“服务写入证明”，需要 owner 侧签名/字段扩展，这里是**未实现**的。

### 本轮实测命令（实际 returncode）

| 命令 | 结果 |
| --- | --- |
| `wsl PYTHONDONTWRITEBYTECODE=1 NYAIRO_WORK_REVISIT_ROOT=/tmp/wrge python3 tests/test_v04_work_revisit_history.py` | `Ran 16 tests … OK`，EXIT=0 |
| `wsl python3 -m unittest tests.test_phone_projects_api tests.test_phone_reading_owner tests.test_v04_shared_practice_phone tests.test_v04_shared_practice_service` | `Ran 98 tests … OK`，EXIT=0 |
| `py -3.13` 对 `phone/service.py`、`private_project_service.py` 的 `ast.parse` | `AST_OK`，EXIT=0 |

### 仍未完成（与 §7 合并阅读）

手机界面屏（app.js）未接新路由；真实 Edge/CDP 点击未跑；正常 Life 循环（候选→AG1→AG2→AR0）把旧版正文
送进 actual writer request 未实现；同实例 **Life runtime** 重启（不是 PhoneService 对象重建）未做；
owner 读+写复合边界未扩。真实产品模型 calls 仍为 0。
