# v0.4 阶段 B · 资料库 + 研究工作台 + 手机面（实现结果）

状态：已实现（本地，未发布、未部署、未提交）。
范围：`components/life/scripts/research_workspace_service.py`（新增）、
`components/phone/{service.py,server.py,web/**}`、本轮新增测试与笔记、本文件。
复用而不新建：**所有资料与研究仍然是同一 owner 的 `ManagedArtifact`**，写路径只经
`PrivateProjectService`（它自己只经 `LifeArtifactPort`）。没有新表、没有新数据库、没有第二份 Memory。

## 1. 内容标记（公开、可检测）

| 工件 | 第一行 | JSON 至少含 |
| --- | --- | --- |
| 资料 | `<!-- nyairo-material:v1 {JSON} -->` | `source_kind`（`user_supplied` / `fictional`）、`origin`（来源声明）、`title`（可选） |
| 研究 | `<!-- nyairo-research:v1 {JSON} -->` | `question`、`source_refs`（每项 `artifact_id` + `source_revision`）、`scope`、`uncertainty`、`title`（可选） |

* 标记只在**第一行**，正文从第二行后的空行开始；`_split_marker` 只认第一行、只剥一个分隔空行。
* 研究正文是暂定研究笔记，可由既有 AR0 `commit_version` 增量续写；`update_research` 每次提交都用
  owner 里那份 marker 重建第一行（question / 引用不被 writer 改写），正文原样保留。
* 资料是**只读材料**：导入只会 `create_artifact` 新建，绝不 `commit` 覆盖既有阅读材料。

## 2. 固定 Python API（A 直接调用，签名不再改）

```python
class ResearchWorkspaceService:
    def __init__(self, projects, *, reading_artifact_id=None, private_items_artifact_id=None)

    # 资料
    def list_materials(self, *, query: str = "", limit: int = 12) -> dict
    def import_material(self, *, title, text, source_kind, origin, operation_key) -> dict
    def read_material(self, artifact_id, *, expected_revision=None) -> dict

    # 研究
    def create_research(self, *, question, source_refs, title=None, scope="",
                        uncertainty=DEFAULT_UNCERTAINTY, operation_key) -> dict
    def list_research(self, *, limit: int = 12) -> dict
    def read_research(self, artifact_id) -> dict
    def update_research(self, artifact_id, *, content, expected_head, operation_key,
                        scope=None, uncertainty=None) -> dict
    def close_research(self, artifact_id, *, expected_revision, operation_key) -> dict

    def describe(self) -> dict            # 接线自检，不读 owner

# 环境工厂（与 private_projects_from_env 同一套 env 名）
def research_from_env(port=None, env=None) -> ResearchWorkspaceService | None

# 纯 helper（A 不必复制第二套 marker）
def parse_research_content(content) -> dict     # marker_present / parse_error / question /
                                                # source_refs / scope / uncertainty / title / body
def build_research_content(question, source_refs=(), scope="", uncertainty=..., body="", title=None) -> str
def is_research_content(content) -> bool        # 有效 marker 才算
def parse_material_content(content) -> dict | None
def is_material_content(content) -> bool
def build_material_content(*, text, source_kind, origin, title=None) -> str
```

**envelope 形状**（与项目服务同词表）：

```jsonc
{"ok": true,  "status": "ok|applied|already_applied", "source": "ManagedArtifact", "data": {...}}
{"ok": false, "status": "conflict|not_found|rejected|unknown|unavailable",
               "reason_code": "...", "source": "ManagedArtifact", "data": {...}}
```

* `ok` 是纯读成功（`list_*` / `read_*`）；写成功是 `applied` / `already_applied`。
* 编程错误（形状不对、缺 CAS、超界）抛 `ResearchContractRefused`；owner 说了什么永远不抛。
* UNKNOWN 由 `PrivateProjectService` 按**原 key** `resolve_operation` 对账后才谈重试
  （`reconciled` / `retryable` 透传），本模块不换键重发。

### 关键 data 字段

* `list_materials().data`：`materials[{artifact_id,title,source_revision,source_kind,origin,excerpt,
  revision,version_number,lifecycle,availability,kind,read_only,updated_at}]`、`query`、`count`、
  `matched`、`truncated`、`scanned`、`skipped{research,private_items,other}`、`reading_artifact_id`。
  既有 `reading_artifact_id` 作为**只读资料**出现在列表里（`source_kind="reading_material"`,
  `read_only=true`），可检索、可引用，导入不会覆盖它。
* `read_material().data.material`：上面的字段 + `text`（**已剥掉 marker 的操作者正文**）、
  `content_available`、`availability ∈ AVAILABLE|ARCHIVED|UNAVAILABLE`。
  `expected_revision` 可给 version id 字符串或 artifact revision 整数；不符 → `status=conflict`,
  `reason_code=MATERIAL_REVISION_MISMATCH`，并带回当前 `source_revision`。
* `create_research().data.project`：`artifact_id`、`title`、`content`（含第一行 marker）、
  `question`、`source_refs`、`scope`、`uncertainty`、`head_version_id`、`version_number`、
  `source_checks`、`not_memory=True`、`stable_belief=False`。
* `read_research().data.project`：再加 `body`（marker 之后的正文）、`content_chars`、
  `sources_current`、`marker_present`、`marker_version`、`note`；
  `source_checks[] = {artifact_id, source_revision, status, reason_code, title, kind, source_kind,
  current_source_revision, lifecycle, checked}`。
* `close_research().data`：`lifecycle=ARCHIVED`、`revision`、`content_kept=True`、`versions_kept=True`。

### 来源版本的三态

| status | 何时 | reason_code |
| --- | --- | --- |
| `CURRENT` | 引用版本 == 当前 head | `CURRENT` / 创建时只点 id 则为 `CURRENT_AT_CREATION`（并填上当时的 head） |
| `STALE` | 工件还在、还是资料，但 head 已经变了 | `SOURCE_REVISION_MOVED`（同时给 `current_source_revision`） |
| `UNAVAILABLE` | 读不到 / 不是资料 / 已归档撤回 / 没有版本 | `ARTIFACT_NOT_FOUND`、`SOURCE_NOT_A_MATERIAL`、`SOURCE_ARCHIVED`、`SOURCE_REVISION_UNKNOWN` |

* `create_research` 先核对每个来源：任一 `UNAVAILABLE` → `status=rejected`、
  `reason_code=RESEARCH_SOURCE_UNAVAILABLE`，**不建任何工件**（不虚构"已取得内容"）。
* `STALE` 允许创建：引用的是调用方点名的那个版本，页面与详情都会照实标 STALE，不会说成当前已核实。
* 关闭是 `archive`：正文、每个版本、引用全留；关闭后再 `update_research` → `rejected / RESEARCH_ARCHIVED`。

### A 的接入

* 环境工厂读的 env 名与 `private_projects_from_env` 相同：`NYAIRO_LIFE_WORKSPACE_ID`、
  `NYAIRO_ARTIFACT_GRANT`、`NYAIRO_LIFE_SUBJECT`（或 CHIYO_/LIFE_ 前缀组）、
  `NYAIRO_LIFE_SUPPLY_SOCKET`；另加 `NYAIRO_READING_ARTIFACT_ID` /
  `NYAIRO_PRIVATE_ITEMS_ARTIFACT_ID`（也可用 `NYAIRO_PHONE_READING_ARTIFACT`、
  `NYAIRO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID` 等别名）。缺一件 → 返回 `None`，不升半坏对象。
* `port` 可传现成 `LifeArtifactPort`、socket 路径字符串，或省略（由 env 建）。
* 资料工件用 `is_material_content()` / `research_workspace_service.parse_material_content()`
  识别，别把资料当可续写作品候选；研究工件用 `is_research_content()` 识别。

## 3. 手机 HTTP 面（新增）

| 方法 | 路径 | 请求 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/phone/materials?query=&limit=` | 只这两个 query，各最多一次 | 资料列表（不含正文），纯读 |
| GET | `/api/phone/materials/<artifact_id>` | — | 一件资料的正文 + 来源版本；纯读 |
| POST | `/api/phone/materials` | `{title,text,source_kind,origin,operation_key}` | 导入文本，新建工件；CSRF 必需 |
| GET | `/api/phone/research?limit=` | 只 `limit` | 研究列表（不含正文），纯读 |
| GET | `/api/phone/research/<artifact_id>` | — | 正文 + 引用 + 每个来源 CURRENT/STALE/UNAVAILABLE |
| POST | `/api/phone/research` | `{question,source_refs,title?,scope?,uncertainty?,operation_key}` | 先核对来源再建 |
| POST | `/api/phone/research/<id>/updates` | `{content,expected_head,operation_key,scope?,uncertainty?}` | CAS 续写，marker 由服务重建 |
| POST | `/api/phone/research/<id>/close` | `{expected_revision,operation_key}` | 归档（不是删除） |

* 保留既有全部闸门：只 loopback、Host/Origin/Sec-Fetch-Site、CSRF、请求体白名单、
  `CLIENT_IDENTITY_REFUSED`（body 里出现 `artifact_id`/`subject`/`price`… 一律 409）。
* `source_refs` 是**唯一**允许出现 artifact id 的嵌套位置，每项只收 `artifact_id` + `source_revision`
  两个键（多一个键 → `UNKNOWN_FIELD:source_refs.<key>`），最多 12 件；是否真是可读资料由服务核对。
* GET 一律纯读（有测试盯着 owner 侧零写）。写操作照旧记 `phone/operations.jsonl`
  （`material_import` / `research_create` / `research_update` / `research_close`）。
* `GET /api/phone/projects` 的行新增 `artifact_type ∈ project|material|research|unknown` 与
  `continuable`：类型只来自工件第一行的 marker（最多核对 50 行），**`private_project_service`
  一个字没改**。研究/资料不会被当作可续写作品：`POST .../projects/<id>/commits` 对它们回 409
  `PROJECT_IS_A_RESEARCH` / `PROJECT_IS_A_MATERIAL`（否则会改写 marker）。

## 4. 手机页面

新增屏幕（`SCREENS`）：`materials`（列表 + 检索 + 导入入口）、`material`（正文/来源声明/版本）、
`material-import`（标题、文本类型、来源声明、正文）、`research`（列表）、
`research-item`（问题/范围/未知/来源核对/正文/续写/关闭）、`research-create`（问题 + 复选具体资料）、
`research-edit`（正文 + 范围/未知，CAS head 只读显示）。
主屏「生活链」行内新增两个按钮「资料库」「研究工作台」；**不加第九个 tile**、`APP_ORDER` 与
`HOME_ORDER` 一字未改。

界面铁律照着旧面写：所有服务文本先 `sanitize*` 再用 `textContent`；GET 不写；写必须 CSRF；
每次写复用同一个 `operation_key`（`MATERIAL_SCOPES`），未知保留原 key 用 `/operations/<key>` 对账；
草稿（资料正文、研究正文）只在内存，不写 localStorage/sessionStorage；来源状态用
「当前版本 / 来源已更新 / 读不到·已撤回」三档 chip，`sources_current=false` 时屏上明写
「旧结果不等于当前已核实」；研究详情常驻一句「不是 Memory，也不是稳定信念」。

新增纯函数（`NyairoPhone` 上可直接用）：`materialListView`、`materialDetailView`、
`materialRowView`、`validateMaterialDraft`、`buildMaterialImportPayload`、`materialPath`、
`materialsListPath`、`researchListView`、`researchDetailView`、`researchRowView`、
`sourceCheckView`、`validateResearchDraft`、`validateResearchUpdateDraft`、
`buildResearchCreatePayload`、`buildResearchUpdatePayload`、`buildResearchClosePayload`、
`researchPath`、`researchUpdatesPath`、`researchClosePath` 以及常量
`MATERIAL_TITLE_MAX/MATERIAL_TEXT_MAX/MATERIAL_ORIGIN_MAX/MATERIAL_QUERY_MAX`、
`RESEARCH_QUESTION_MAX/RESEARCH_SCOPE_MAX/RESEARCH_UNCERTAINTY_MAX/RESEARCH_CONTENT_MAX/
RESEARCH_SOURCES_MAX/RESEARCH_DEFAULT_UNCERTAINTY`、`MATERIAL_SOURCE_KINDS`、`SOURCE_KIND_TEXT`、
`CHECK_STATUS_TEXT`、`MATERIAL_SCOPES`。

样式：`.material-text` / `.research-text`（`white-space: pre-wrap`、可滚动）、`.list-note`（单行省略）。

## 5. 测试与命令（本轮实际跑过）

| 命令 | 结果 |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_v04_research_workspace` | OK，22 tests，exit 0 |
| `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_phone_research_api` | OK，9 tests，exit 0 |
| `node --test tests/test_phone_research_ui.mjs` | pass 9 / fail 0，exit 0 |
| `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_phone_projects_api tests.test_phone_api tests.test_v04_research_workspace tests.test_phone_research_api` | OK（51 tests，13 skipped，skip 是既有条件跳过），exit 0 |
| `node --test tests/test_phone_projects_ui.mjs tests/test_phone_ui.mjs tests/test_phone_research_ui.mjs` | pass 49 / fail 0，exit 0 |

覆盖点：资料分类/检索/导入幂等/读取的版本拒绝、资料 marker 可检测、研究创建先核对来源（读不到即拒）、
marker 在第一行且含四项、正文不冒充已读、续写保留 marker 与引用、CAS 缺失即拒/陈旧 head 是 conflict、
来源更新变 STALE、来源归档变 UNAVAILABLE、关闭保留正文与全部版本、关闭后拒绝续写、UNKNOWN 先对账、
环境工厂与纯 helper；手机侧 CSRF/白名单/嵌套 source_refs/NUL/纯读/类型提示/拒绝把研究或资料当项目续写；
UI 纯函数与样式、旧七个 app 顺序未变。

**本轮未修、与本次改动无关的既有失败**：`python -m unittest tests.test_v04_private_projects_stage`
里 `LifeOwnerAdapterTest.test_gateway_creates_one_project_through_the_owner`（第 472 行
`"artifact:" + artifact_id` 把 FakePort 已带前缀的 id 又加了一次前缀）失败；该断言与实现都在上一阶段
的文件里，本轮没有触碰 `private_project_service.py`，因此保留原状交给父级判定。

## 6. 已知限制（照实写）

1. **不是全网研究**：本阶段不联网抓取。资料只来自操作者提供的文本（`source_kind=user_supplied|fictional`），
   `origin` 是**声明**，不是真实性认证；`import_material` 没有 http 客户端。
2. **列表扫描有界**：`list_materials` / `list_research` 每件工件读一次 head（`MAX_SCAN=200`，
   超出的不假装读过），因此列表不是"全库索引"；`truncated` 照实报。
3. **研究/资料的类型识别靠第一行 marker**：手写一件恰好以同样标记开头的普通作品会被当成研究
   （这是标记契约的代价，选择公开可检测而不是隐式元数据）。
4. **手机 `/state` 不新增 app 项**：资料/研究屏以连接阶段为闸门（`loading/offline/error` 挡屏），
   不去 `/state` 里再跑一次有界扫描，避免每次 state 都读一遍 owner。
5. **研究不能改名、不能删除**：owner 的 `commit_markdown` 没有改名动词，B 不假装有；
   关闭即归档，正文与版本全留。
6. **来源变化不会自动重读**：STALE/UNAVAILABLE 只是标记；要不要重读、要不要改写研究，由 A 的
   自主路径或操作者决定，本模块不代替。
7. **`open_at` / 抓取时间 / 页级核对没有**：研究正文只写"问题 / 范围 / 引用声明 / 暂定发现 / 未知"，
   从不声称读过某一页。
