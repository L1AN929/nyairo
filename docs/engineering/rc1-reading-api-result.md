# rc1 阅读工作台（P 侧）：手机正文/停点/页注接线结果

写集：`components/phone/service.py`、`components/phone/server.py`、`chiyo_bundle/phone_tool.py`；
新增 `tests/test_phone_reading_api.py`、`tests/test_phone_reading_tool.py`、本文件、
[用户指南](rc1-reading-userguide.md)、note
`.agents/notes/implemented/feature/2026-10-22-rc1-reading-phone-api.md`。

`scripts/phone.py` 与 `components/phone/demo.py` **一个字节都没改**：实例里早就有
`reading_artifact_id`（`demo.py:_instance_payload`），施工单也明确「无需初始化、无需 CLI 导入
另一路（页面 POST 已能真实导入）」。没有碰 `components/phone/web/**`（U 的写集）、
`components/life/**`（C 的写集）、`components/world/**`、`MANIFEST.json`、两份 rc1 移动 fix
脚本；没有 stage/commit/push；没有物理删除任何文件（含 socket/pyc/测试夹具）。

补接一轮（定点漏接）：U 的阅读屏需要旧 `GET /api/phone/notes` 里 memo 的 `reading_anchor` 才能
导航回「当初那一版那一页」，而 P 的 notes 行此前没有返回它。这一轮只改
`components/phone/service.py`（新增 `memo_reading_anchor()` + notes 行可选透传）、
`tests/test_phone_reading_api.py`（新增 `NotesReadingAnchorTest` 3 项）与本文件；web/Life 一个
字节没碰（普通 notes 编辑不抹锚点由 C 的 `revise_memo` 负责）。

## 1. 结论

| 缝 | 状态 |
|---|---|
| 实例 `reading_artifact_id` → C 的 `PhoneReading` | 已接；真实 facade 签名一致性窄测 1 项 |
| `GET /api/phone/reading/material[?page=N]` 只读真实正文 | 已接，桩测 9 项（含零写、Query 白名单、未绑定材料） |
| `POST /api/phone/reading/material` 显式导入正文 | 已接，桩测 6 项 + owner 提示 2 项 |
| `POST /api/phone/reading/positions` 显式保存停点 | 已接，桩测 4 项 |
| `POST /api/phone/reading/annotations` 页注（note/unclear） | 已接，桩测 5 项 |
| `GET /api/phone/reading/operations/<key>` 原 key 对账 | 已接，桩测 5 项 |
| `GET /api/phone/notes` 的 memo 可选 `reading_anchor` | 已接，桩测 3 项（锚点透传 / 无锚点兼容 / state 不带正文） |
| `chiyo_phone` 五个阅读动作 + 两把钥匙 | 已接，工具测 19 项 |
| Supply 的真实版本/CAS/取得证据/`needs_reread` | **C 的**（P 只转发，不冒充） |

真实 HTTP 形状的用例共 41 项（`tests/test_phone_reading_api.py`），其中 2 项需要 POSIX
`LifeArtifactPort`（真实 C facade 端到端），Windows 上按既有惯例 skip。其余 39 项全部真实
绑定 loopback、真实 `create_server`、真实 CSRF/Host/Origin 闸门（含 3 项 notes 锚点回归）。

## 2. P 消费的 C 接口（精确，逐字对齐真实文件）

```python
PhoneReading(port: LifeArtifactPort, *, reading_artifact_id, private_items_artifact_id,
             workspace_id, grant_id, clock=None)
PhoneReading.material(page: Optional[int] = None) -> dict
PhoneReading.replace_material(content, expected_revision, operation_key) -> dict
PhoneReading.save_position(page, source_revision, expected_head, operation_key) -> dict
PhoneReading.annotate(kind, page, source_revision, expected_head, operation_key,
                      title=None, body=None, note=None) -> dict
PhoneReading.operation(operation_key) -> dict
```

* `port` 是**已构造的** `LifeArtifactPort`（`PhoneService._port()`）：构造 port 需要 `subject`，
  而 C 的签名里没有 `subject`，所以第一个位置参数只能是 port 对象本身；C 用
  `isinstance(port, LifeArtifactPort)` 守卫。
* 返回信封 `{ok, status, data, reason_code?, http_status?}`；`http_status` 是 C 给 P 的转发
  提示（见 §4）。
* C 的 `understanding` 恒 `"UNKNOWN"`；`data` 里的字段 P 一字不改。

## 3. HTTP 契约（P 面）

统一成功信封 `{ok:true, status:"applied"|"already_applied", data:{...}}`；新阅读数据全部在
`data` 里，不散落在顶层。

### 3.1 `GET /api/phone/reading/material?page=N`

* `page` 可选。**不带**时 P 把 `None` 交给 owner —— owner 用「当前版本最近一次显式保存的停点」
  回答，没有停点才 1；P 不预填 1、不按最高页猜。
* Query 白名单只有 `page`：未知键 → 400 `UNKNOWN_QUERY:<k>`；重复 → 400 `QUERY_REPEATED:page`；
  空值/`-1`/`+2`/`01`/`1.5`/`true`/非数字 → 400 `PAGE_INVALID`；`>64` → 400 `PAGE_OUT_OF_RANGE`。
  一律在 owner 之前拒绝。
* 闸门与 `/world` 相同：`Host ∈ {127.0.0.1, localhost}`、`Origin` 同源、`Sec-Fetch-Site ∈
  {same-origin, none}`；**不要 CSRF**（GET）。
* **零写**：不写文件、不追加 `operations.jsonl`、不创建材料、不动停点。
* 返回 `data`：`available`、`artifact_id`、`source_id`（`artifact:<id>`）、`source_revision`
  （真实 head version id）、`version_number`、`title`、`page`、`pages`、`text`（该页真实正文）、
  `content_available`、`private_head`（事项真实 head，供下次 CAS）、`bookmark`
  （`{page, source_revision, needs_reread}` 或 `null`）、`annotations[]`
  （`{id, kind, title?, body?, note?, source_id, source_revision, page, created_at}`）、
  `understanding:"UNKNOWN"`；C 还会带 `private_items_status`、`http_status`，P 原样转发。
* `page` 超出真实页数：owner 的 `rejected / PAGE_NOT_PRESENT`（409）。
* 未绑定材料（实例没有 `reading_artifact_id`）：503 `READING_MATERIAL_UNAVAILABLE`，带
  `available:false` / `content_available:false` / `bookmark:null` / `annotations:[]`，**不碰 facade**。
* Supply socket 不在：503 `SUPPLY_UNAVAILABLE`。

### 3.2 `POST /api/phone/reading/material`

```json
{"content": "…%%PAGE%%…", "expected_revision": "<当前真实 head version id>", "operation_key": "…"}
```

* `content`：`str`，1..24000 **UTF-8 bytes**、非空白、无 NUL；`\r\n`/`\r` 归一成 `\n`；
  `%%PAGE%%` 原样透传（P 不重新分页，每页 ≤12000 bytes 与 ≤64 页由 owner 判）。
  超界/空白/非字符串 → 400 `READING_CONTENT_INVALID`。
* `expected_revision`：非空字符串（≤128）与 owner 真实 head 不符 → owner `rejected /
  READING_REVISION_CONFLICT`（409）；缺 → 400 `EXPECTED_REVISION_INVALID`。
* `operation_key`：`[A-Za-z0-9._:-]`，1..128，缺 → 400 `OPERATION_KEY_REQUIRED`。
* 闸门：CSRF（`X-CSRF-Token`，常量时间比较）+ `Content-Type: application/json` +
  `Content-Length` 1..65536 + 身份/未知字段白名单。
* 返回 `data`：`artifact_id`、`source_id`、`source_revision`、`version_number`、`pages`、`page:1`、
  `page_bytes[]`、`content_chars/bytes`、`title`、`operation_key`、`owner_status`、`effect_state`、
  `version_created`、`reconciled`、`detail`、`bookmark`（旧停点，通常已 `needs_reread=true`）、
  `private_head`、`understanding:"UNKNOWN"`。
* 同 key 同正文重放 → `already_applied`（不落第二版）；同 key 换正文 → 409
  `READING_OPERATION_KEY_REUSED`。

### 3.3 `POST /api/phone/reading/positions`

```json
{"page": 2, "source_revision": "<该材料真实 head>", "expected_head": "<private_head>", "operation_key": "…"}
```

* `page` 严格整数非 bool（`true`/`2.0`/`"2"`/`0` → 400 `PAGE_INVALID`；`>64` → 400
  `PAGE_OUT_OF_RANGE`）；`source_revision`/`expected_head` 必填（400 `SOURCE_REVISION_INVALID` /
  `EXPECTED_HEAD_INVALID`）。
* owner 会**真实读该页**再把取得证据（`receipt_ref=read:<artifact>:<rev>:page-N`、`chars`）与位置
  一次性提交进同一份私人文档；回到前页是合法动作。
* 返回 `data`：`page`、`source_revision`、`private_head`、`understanding:"UNKNOWN"`、
  `operation_key` + owner 的 `acquisition_id`/`position_id`/`effect_state` 等。
* 版本不符 → 409 `READING_REVISION_CONFLICT`；页不存在 → 409 `PAGE_NOT_PRESENT`；
  `expected_head` 过期 → 409 `READING_HEAD_CONFLICT`。

### 3.4 `POST /api/phone/reading/annotations`

```json
{"kind": "note",    "page": 2, "title": "…", "body": "…", "source_revision": "…", "expected_head": "…", "operation_key": "…"}
{"kind": "unclear", "page": 2, "note": "…",               "source_revision": "…", "expected_head": "…", "operation_key": "…"}
```

* `kind` 只认 `note|unclear`（其余 → 400 `ANNOTATION_KIND_INVALID`，大写 `NOTE` 也拒）。
* 逐 kind 字段：`note` 必须 `title`+`body`（400 `ANNOTATION_TITLE_REQUIRED` /
  `ANNOTATION_BODY_REQUIRED`），带 `note` → 400 `UNKNOWN_FIELD:note`；`unclear` 必须 `note`
  （400 `ANNOTATION_NOTE_REQUIRED`），带 `title`/`body` → 400 `UNKNOWN_FIELD:title|body`。
  `title` ≤200 **UTF-8 bytes**、`body`/`note` ≤8000 **UTF-8 bytes**（C 的 `PhoneReading._text`
  就是按字节判的；P 用同一单位，所以 P 放行的不会被 owner 再以「超界」拒一次）。
* **必须先取得该版本该页**：没有取得证据 → 409 `PAGE_NOT_ACQUIRED`（界面上先保存停点）。
* 返回 `data`：`kind`、`id`、`page`、`source_id`、`source_revision`、`anchor{source_id,
  source_revision, page}`、`private_head`、`understanding:"UNKNOWN"`、`operation_key`、
  `effect_state`、`owner_status`、`detail`。
* 注释正文只出现在这条私人读取面（`GET material` 的 `annotations[]`）与旧 `GET /notes`，
  不进 `/state`、不进旧阅读元数据列表、不提高任何 disclosure excerpt 上限。

### 3.5 `GET /api/phone/reading/operations/<key>`

* 键字符集 `[A-Za-z0-9._:-]{1,128}`（不合法 → 404 `NOT_FOUND`，不触达 owner）。
* 原样查 owner：已落地 → 200（`data.operation_key/action/effect/applied/reason/…`）；
  未决 → 202 `unknown`；从未落地 → 404 `not_found`。
* **没有换新 key 重试的代码路径**（原 key 是唯一身份）。

### 3.6 `GET /api/phone/notes` 的 memo 锚点（私人 owner 面，只读）

页面上的锚点笔记要能导航回**它当初的那一版那一页**，所以每条 memo 行多一个**可选**
`reading_anchor`：

```json
{"id": "memo:…", "title": "…", "body": "…", "revision": 2, "revisions": 2,
 "created_at": "…", "updated_at": "…", "observed_object_ref": null,
 "reading_anchor": {"source_id": "artifact:<reading artifact>",
                    "source_revision": "<这一版的真实 version id>", "page": 2}}
```

* 只对 `ITEM_MEMO` 行出现；`source_acquisition` / `reading_position` / `unclear_marker` 等行
  不带这个键（它们不是笔记）。
* 三个字段就是 Supply 的 protocol id 原样：P 不补前缀、不改写、不截断（`service.memo_reading_anchor`）。
* **无锚点兼容**：旧 memo 没有这个键，行形状与以前逐字段一致（不是 `null`，也不编一个页码）。
* 锚点残缺（缺 `source_revision`、`page` 是 `"2"`/`true`/`0`、id 非字符串或空）→ 该行**省略**
  锚点，不修、不猜、也不让整个列表 500。
* 锚点是元数据；正文只在 `GET /notes` 的 `body`（唯一个人读取面）与 `GET material` 的
  `annotations[]` 里，`/state` 与旧阅读元数据列表都只报 `item_count` 之类计数，**不带正文、
  不带锚点**。普通 notes 编辑不抹锚点由 C 的 `revise_memo` 负责（P 不改写）。

## 4. 状态码与提示

沿用既有映射：`applied`/`already_applied` → 200、`rejected`/`disabled` → 409、
`unavailable` → 503、`unknown` → 202、`not_found` → 404。新增一条有界规则：C 的
`http_status` 提示**只在冻结集合 `{200,202,400,404,409,413,503}` 内、且不翻转语义**时采纳
（成功只能 200/202；拒绝只能 400/404/409/413/503），其余回落到状态映射。于是「输入根本不是
一段正文/一页/一个 kind」是 **400**，而任何 owner 都无法把拒绝说成 200。

其它诚实失败面：facade 缺方法 → 503 `PHONE_FACADE_UNIMPLEMENTED {method}`；facade 抛非信封
异常 → 503 `PHONE_READING_ADAPTER_ERROR`；`PhoneReadingError`（C 判定 facade 被误用）→ 冒泡成
500 `PHONE_INTERNAL_ERROR`；未绑定材料 → 503 `READING_MATERIAL_UNAVAILABLE`。

## 5. 权限与负例（真跑过的）

| 负例 | 结果 |
|---|---|
| `Host: evil.example` / `Origin: http://evil.example` / `Sec-Fetch-Site: cross-site` | 403 `HOST_NOT_ALLOWED` / `ORIGIN_NOT_ALLOWED` / `CROSS_SITE_REFUSED`，facade 零调用 |
| 缺/错 `X-CSRF-Token` | 403 `CSRF_TOKEN_MISSING` / `CSRF_TOKEN_INVALID`，零调用、零日志行 |
| body 带 `artifact_id`/`uid`/`owner`/`source`/`workspace_id` | 409 `CLIENT_IDENTITY_REFUSED:<k>`，零调用 |
| body 带 `title`/`understanding`/`url`/`page`（进 material） | 400 `UNKNOWN_FIELD:<k>` |
| `page: true / "2" / 2.0 / 0 / null / 65` | 400 `PAGE_INVALID` / `PAGE_OUT_OF_RANGE`，零调用 |
| `kind: "NOTE" / "memo" / 1 / true` | 400 `ANNOTATION_KIND_INVALID` |
| 24001 bytes 正文 / 8001 CJK 字 / 空 / 全空白 / 含 NUL | 400 `READING_CONTENT_INVALID`，零调用 |
| 未绑定 `reading_artifact_id` 的根 | 503 `READING_MATERIAL_UNAVAILABLE`（`available:false`、`bookmark:null`、`annotations:[]`），facade 零调用 |
| notes 行带残缺锚点（缺 `source_revision`、`page:"2"`、`page:true/0`、id 空或非字符串） | 该行省略 `reading_anchor`，不修、不猜、不 500；其余行照常 |
| `/state` 想找 memo 正文或锚点 | 不出现：`apps.notes.data` 只有 `item_count` + `app`，`body`/`items`/`reading_anchor` 均无 |
| 工具：`memory=true` + 无 `phone.private_artifacts` | `PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`，零请求 |
| 工具：`write_enabled=false` 时写动作 | `PHONE_WRITE_DISABLED`，零请求 |
| 工具：`platform=local` / 未绑定 DM / `parent_session_id` | `PHONE_ENTRY_NOT_AUTHORIZED` / `PHONE_DELEGATED_SOURCE_REFUSED`，零请求 |
| 工具：`reading_import` 带 `artifact_id`/`path`/`title`/`understanding` | `UNKNOWN_FIELD` + `fields=[…]`，零请求 |
| 工具：`reading_operation` 未知键 | 202 `unknown`（原 key 回读），不换键 |

## 6. 工具动作（`chiyo_phone`）

`action` 新增 `reading_material`（只读，可带 `page`）、`reading_import`、`reading_position`、
`reading_annotate`（写，需 `write_enabled`）、`reading_operation`（只读对账）。五个全部属于
`PRIVATE_ARTIFACT_ACTIONS`：受限记忆 + 无 `phone.private_artifacts` 时一起拒。schema 新增
`page`（integer 1..64）、`kind`（`note|unclear`）、`content`（maxLength 24000）、
`expected_revision`、`note`（maxLength 8000，按 C 的字节上限），**不提供** `artifact_id`/`path`/`url`/
`authority`/`understanding`。文案区分「导入 ≠ 读过」「位置 ≠ 理解证明」「页注 ≠ 读懂」，
`PAGE_NOT_ACQUIRED` 时提示先 `reading_position`。

## 7. 验证命令与实际结果

全部在仓库根 `D:\nyairo-rc1-body-backend` 执行，`PYTHONDONTWRITEBYTECODE=1`，夹具根指向会话
scratch（`NYAIRO_READING_TEST_ROOT=$env:PI_SCRATCH_DIR\reading-fixtures`，**保留不删**）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python -m unittest tests.test_phone_reading_api` | 0 | `Ran 41 tests … OK (skipped=2)`（含 3 项 notes 锚点回归） |
| `python -m unittest tests.test_phone_reading_api.NotesReadingAnchorTest -v` | 0 | `Ran 3 tests … OK`（锚点透传 / 无锚点兼容 / state 不带正文） |
| `python -m unittest tests.test_phone_reading_tool` | 0 | `Ran 19 tests … OK` |
| `python -m unittest tests.test_phone_reading_api tests.test_phone_reading_tool` | 0 | `Ran 60 tests … OK (skipped=2)`（本轮两个新套件一起跑） |
| `wsl -d Ubuntu -- bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && env -u PI_SCRATCH_DIR -u NYAIRO_READING_TEST_ROOT PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_phone_reading_api.RealFacadeConformanceTest tests.test_phone_reading_api.NotesReadingAnchorTest'`（父级环境，POSIX） | 1 → **0** | 修复前：`Ran 3 tests … FAILED (failures=1)`，第 3 项 `AssertionError: fixture root already exists: /tmp/nyairo-reading-pi/rc1-reading-real-18660-614624`（父级日志 `reading-parent-posix-conformance.log`）；修复后：`Ran 6 tests … OK`、`EXIT_CODE=0`（日志 `reading-parent-posix-fixed.log`）。6 项 = 该 class 3 + `NotesReadingAnchorTest` 3，POSIX 上**无 skip** |
| Windows 上同一条命令（两个 class） | 0 | `Ran 6 tests … OK (skipped=2)`（2 项真实 `LifeArtifactPort` 按既有惯例 skip），日志 `reading-anchor-six.log` |
| `python $env:PI_SCRATCH_DIR\reading_root_stress.py`（同进程同前缀连建 200 个根） | 0 | `ok: 200 unique retained roots`（全部不同名、全部存在、全部保留不删） |
| `python -m unittest tests.test_phone_api tests.test_phone_tool` | 0 | `Ran 24 tests … OK (skipped=24)`（旧手机套件 POSIX-only） |
| `python -m unittest tests.test_room_living_api tests.test_room_living_tool` | 0 | `Ran 39 tests … OK (skipped=2)`（房间链未受影响） |
| `python -m unittest tests.test_phone_reading_tool tests.test_room_living_api tests.test_room_living_tool` | 1（偶发） | 58 项里偶发 1 个 error，两次分别是 `test_room_living_api.StorePurchaseTest.test_body_shape_gates` 与 `test_csrf_is_required_and_checked`：Windows 对「守卫拒绝时先不读请求体、直接回 403/400」的既有行为报 `ConnectionAbortedError`（既有平台 flake，换一个用例命中；单独跑该套件两套 39 项全绿，本轮新套件 60 项也全绿） |
| `python -m unittest tests.test_room_living_serve_flag` | 1 | 12 个 error 全部是 `os.geteuid()` 在 Windows 不存在（既有平台问题，该文件不在本写集） |
| `npx tsx …/verify-agent-note-tree.ts`（cwd=仓库根） | 0 | `ok: 44 note(s) tree and relative links verified` |
| `npx tsx …/verify-agent-note-format.ts`（cwd=仓库根） | 0 | `ok: 44 note(s) verified` |

日志留在 scratch：`reading-api-test*.log`、`reading-tool-test.log`、`affected-phone-tests.log`、
`affected-room-tests*.log`。

## 8. 未做 / 已知限制 / 偏差

* **没有跑真实实例端到端**：没有启动任何运行中的手机实例、没有碰 `/tmp/nyaw05r.jdK3q/…`、
  没有在真实 Supply 上导入正文或保存停点。真实「重启后仍停在第 N 页」「新材料版让旧书签
  `needs_reread`」由 C 的 `test_phone_reading_owner.py` 与父级同实例验收覆盖，P 不冒充。
* **真实 C 集成窄测在本机被平台挡了一半**：`life_artifact_port.LifeArtifactPort` 要求 socket
  路径以 `/` 开头，Windows 下构造即抛 `LifeArtifactError`，所以「P 造出真实 facade 并让它回答」
  的两项在 Windows skip；可跑的签名一致性一项（真 import C 的模块、逐参数比对）实际已通过。
  要在 POSIX 上补齐：`python -m unittest tests.test_phone_reading_api.RealFacadeConformanceTest`。
* **64 KiB 请求体边界没放宽**：24000 UTF-8 bytes 的正文以未转义 UTF-8 发送（工具就是这样发）
  最坏约 24 KB；只有把大量 4 字节字符写成 `\uXXXX\uXXXX` 转义的客户端会撞到 64 KiB 而被
  `BODY_TOO_LARGE`（413）。这是有意的：闸门不因新功能区放宽。
* **未知 API 路由的 POST 仍会「读一半就 404」**：Windows 客户端会看到 `ConnectionAbortedError`
  而不是 404 信封（既有行为，本轮不改；本文件的用例改为不带 body 的 POST 以避开平台噪声）。
* **没有做**：正文缓存（浏览器/工具都不缓存）、自动模型读书、理解评分、网络抓取、World 新动词
  或书架容器；`/state` 与旧 `GET /api/phone/reading` 都不含正文。
* **与 C 的 `rc1-reading-contract.md` 逐条对齐后的三处修正**（本轮内完成，已复测）：
  ① 未绑定材料的 reason 用 C 的 `READING_MATERIAL_UNAVAILABLE`（不是自定义的
  `READING_ARTIFACT_NOT_BOUND`），并带上 C 的空材料形状；② 页注上限改用 C 的
  `title ≤200` / `body|note ≤8000` **UTF-8 字节**（原先沿用私人面的 80/4000 字符会拒绝 C 允许
  的合法批注）；③ 采纳 C 的 `http_status` 提示（C 的契约 §1 明确说 P 可以只用状态映射，也可以
  用提示值；P 用「有界采纳」两者兼得）。
* **一处起草期波形（记录事实，不是我的改动）**：复测中途 `components/life/scripts/
  private_daily_items.py`（C 的写集）短暂出现 `SyntaxError: unmatched ')'`（第 1255/1256 行重复的
  `record:` 参数行），`GET /api/phone/reading` 因此 500；C 随后自行修好（语法检查通过），我在其
  之后重跑，57 项恢复 `RC=0`。P 侧没有为这个波形加任何兜底代码。
* **父级 WSL 报回的测试侧缺陷（纯测试，已修）**：`tests/test_phone_reading_api.py` 的
  `unique_root()` 原来用 `pid + int(time.time()*1000) % 1000000` 命名，POSIX 上同一毫秒里连续建
  两个同前缀夹具根就会撞名——父级 `RealFacadeConformanceTest` 第 3 项因此
  `AssertionError: fixture root already exists: /tmp/nyairo-reading-pi/rc1-reading-real-18660-614624`
  （`scratch/reading-parent-posix-conformance.log`，退出码 1）。现在改成
  `pid + time.time_ns() + 每进程自增序号`，`if root.exists(): raise` 原样保留（**绝不覆盖、绝不
  删除已有夹具**，也不重试到别的目录），并在签名一致性用例里加了断言：同进程连续两次同前缀
  必然不同名、且两个目录都真实存在。修复后同一 WSL 命令 `Ran 6 tests … OK`、`EXIT_CODE=0`
  （`scratch/reading-parent-posix-fixed.log`，POSIX 上 6 项全跑无 skip）；Windows 上同一条命令
  `Ran 6 tests … OK (skipped=2)`；另有同前缀连建 200 个根的压力脚本 `RC=0`。产品代码零改动。
