# v0.4 阶段 B · 个人实践工作台（回顾 / 做法）+ 手机实践面（实现结果）

状态：已实现（本地，未发布、未部署、未提交）。
范围：`components/life/scripts/personal_practice_service.py`（新增）、
`components/phone/{service.py,server.py,web/app.js,web/style.css}`、本轮新增测试与笔记、本文件。
复用而不新建：**回顾与做法仍然是同一 owner 的 `ManagedArtifact`**，写路径只经
`PrivateProjectService`（它自己只经 `LifeArtifactPort`）；经历只经 A 的 `LifeMemoryService` 读。
没有新表、没有新数据库、没有第二份 Memory、没有新 `AG0` source_kind。
`private_project_service.py`、`research_workspace_service.py`、A 的 Memory 文件、C 的 Life 文件一字未改。

## 1. 第一行标记（B 的唯一定义）

| 工件 | 第一行 | JSON 至少含 |
| --- | --- | --- |
| 回顾 `review` | `<!-- nyairo-practice:v1 {JSON} -->` | `kind`、`title`、`question`、`experience_ids`、`experience_basis`（`experience` / `none`） |
| 做法 `method` | 同上 | `kind`、`title`、`instruction`、`scope`、`scope_missing`、`uncertainty`、`method_state`、`experience_ids`、`review_refs`、`trials`、`revisions` |

* 只认**第一行**；`<!--` / ` -->` 之间是单行、键排序的 JSON。
* 标记之后的正文逐字原样返回（`parse_practice_content(content)["body"]`）；标记与正文之间只剥
  一个分隔空行。
* `trials` 是反例的存放处：`[{outcome, detail, experience_ids, version_id, evidence_usable}]`，
  跟文档的每个版本一起被 owner 保留，修订抹不掉。
* 公开 helper：`parse_practice_content` / `is_practice_content` / `practice_kind` /
  `build_practice_content`（A、C 不需要复制第二套标记定义）。

## 2. 固定 Python API（C 直接调用，签名不再改）

```python
class PersonalPracticeService:
    def __init__(self, projects, memory_service=None)          # projects: PrivateProjectService

    # 回顾
    def list_reviews(self, *, limit: int = 12) -> dict                      # data.projects
    def read_review(self, artifact_id) -> dict                              # data.project + body/checks
    def create_review(self, *, question, title=None, experience_ids=(), operation_key=None) -> dict

    # 做法
    def list_methods(self, *, query: str = "", limit: int = 12) -> dict     # data.projects
    def read_method(self, artifact_id) -> dict                              # data.project + body/checks
    def create_method(self, *, title, instruction, scope="", uncertainty="",
                      experience_ids=(), review_refs=(), operation_key=None) -> dict
    def record_trial(self, artifact_id, *, outcome, detail="", experience_ids=(),
                     expected_head, operation_key=None) -> dict
    def revise_method(self, artifact_id, *, instruction, scope="", uncertainty="",
                      expected_head, operation_key=None) -> dict
    def retire_method(self, artifact_id, *, expected_head, operation_key=None) -> dict

    # 正文续写（AR0 / 界面）
    def update_document(self, artifact_id, *, content, expected_head, operation_key=None) -> dict

    # 自省
    def describe(self) -> dict
    def memory_bound(self) -> bool

def practice_from_env(port=None, env=None) -> Optional[PersonalPracticeService]
# 别名：practice_service_from_env / personal_practice_from_env / from_env
```

信封：`{ok, status, source: "ManagedArtifact", data, reason_code?}`，`status ∈
{ok, applied, already_applied, conflict, not_found, rejected, unknown, unavailable}`。

`data` 里读接口给 `project`（含 `head_version_id` / `revision` / `version_number` /
`lifecycle` / `status`）+ `kind` + `body` + `content` + `content_available` +
`governance{blocked, reason_code, detail}`：
* 回顾：`question`、`experience_ids`、`experience_basis`、`experience_checks`；
* 做法：`instruction`、`scope`、`scope_missing`、`uncertainty`、`method_state`、
  `experience_ids`、`review_refs`、`evidence_checks`、`trials`、`counterexamples`、
  `revisions`、`verified_skill=false`、`stable_belief=false`。

写接口另给 `artifact_id` / `version_id` / `version_number` / `head_version_id` /
`operation_key` / `effect_state` / `reconciled` / `retryable`（有就透传）。

### 状态机（只有四档，没有 SUPPORTED）

| 动作 | 结果状态 | 说明 |
| --- | --- | --- |
| 新建做法 | `TENTATIVE` | 具体做法 + 条件 + 未知 |
| `record_trial(HELPED)` + 可读证据 | `TRIAL` | 试过 ≠ 已验证 |
| `record_trial(NOT_HELPED)` + 可读证据 | `CONTESTED` | 反例留在 `trials`，修订不抹 |
| `record_trial(UNKNOWN)` / 无证据 | 不变 | 无证据时 HELPED/NOT_HELPED 被拒（`PRACTICE_EVIDENCE_REQUIRED`） |
| `revise_method` | `TENTATIVE` | 旧版本留在 owner 历史，`revisions+1` |
| `retire_method` | `RETIRED` | 文档 / 正文 / 反例全保留，无删除；已退休再试 → `rejected / PRACTICE_METHOD_RETIRED` |

### 证据与治理

* `create_review` / `create_method` 前逐条 `memory_service.read_experience(id)`；任何一条不可读
  就 `rejected / PRACTICE_EVIDENCE_UNREADABLE`（一件工件都不建）。
* 读回顾 / 做法时重新核对：经历 +（做法）被引用的回顾（深度 ≤ 4，环检测）。任何一项不可读 →
  **任意嵌套字段**都不带受治理内容：`body=""`、`content=""`、`data.project.content=""`、
  `content_available=false`、`governance.blocked=true`、
  `reason_code=PRACTICE_EVIDENCE_UNAVAILABLE`；回顾的 `question`、做法的
  `instruction` / `scope` / `uncertainty` 一并置空；`trials` 保留
  `outcome` / `version_id` / `evidence_usable`（状态），但 `detail` 置空（依赖受治理内容的说明）；
  `revised_from` 之类只存在于标记里的旧指令不会出现在任何输出字段。元数据（title / head /
  revision / method_state）与 `checks` 照实保留。
* 列表行过**同一道治理**：被挡的行 `governance_blocked=true`、`governance_reason` 有值，
  且 `question`（回顾）/ `instruction` / `scope` / `uncertainty`（做法）为空；检索用的内部
  `_haystack` 只由标题构成（不会因为受治理的词被搜到），响应里不会出现 `_haystack`。
* 被治理挡住的文档不再接受试用 / 修订（同样 `PRACTICE_EVIDENCE_UNAVAILABLE`）；退休仍可执行。
* 被引用的回顾自己若已 blocked，做法上的那条 review check 也会 `usable=false`
  （`reason_code=PRACTICE_EVIDENCE_UNAVAILABLE`）—— 递归传播，不从派生文档漏回正文。
* 无 Memory（`memory_service=None` 或 A 的服务未绑定）时：回顾 / 做法只能以
  `experience_basis=none` 建立；引用经历一律被拒（`PRACTICE_MEMORY_UNBOUND` / A 自己的原因码）。

## 3. 手机 API（P 只转发，零重算）

| 方法 | 路径 | 请求体（白名单） | 说明 |
| --- | --- | --- | --- |
| GET | `/api/phone/memory` | — | Memory 绑定状态（纯读，绝不建库） |
| GET | `/api/phone/memory/experiences?query=&limit=` | — | 她能召回的经历（有界） |
| GET | `/api/phone/memory/experiences/<experience_id>` | — | 一条经历（A 的治理决定是否可读） |
| POST | `/api/phone/memory/enable` | `{operation_key}` | 显式启用；返回 `restart_required`，**不重启任何服务** |
| GET | `/api/phone/practice` | — | 回顾 + 做法 + Memory 状态（一次读） |
| GET | `/api/phone/practice/reviews/<id>` | — | 正文 + head + 每条经历现在的可读性 |
| POST | `/api/phone/practice/reviews` | `{question, title?, experience_ids, operation_key}` | 建一份回顾 |
| GET | `/api/phone/practice/methods?query=&limit=` | — | 做法列表（不含正文） |
| GET | `/api/phone/practice/methods/<id>` | — | 做法 + 状态 + 全部证据核对 |
| POST | `/api/phone/practice/methods` | `{title, instruction, scope, uncertainty, experience_ids, review_refs, operation_key}` | 建 TENTATIVE 草案 |
| POST | `/api/phone/practice/reviews/<id>/documents` | `{content, expected_head, operation_key}` | 续写正文（CAS，标记由服务重建） |
| POST | `/api/phone/practice/methods/<id>/documents` | 同上 | 续写做法正文 |
| POST | `/api/phone/practice/methods/<id>/trials` | `{outcome, detail, experience_ids, expected_head, operation_key}` | 试用评价 |
| POST | `/api/phone/practice/methods/<id>/revisions` | `{instruction, scope, uncertainty, expected_head, operation_key}` | 修订 |
| POST | `/api/phone/practice/methods/<id>/retire` | `{expected_head, operation_key}` | 逻辑退休（不是删除） |

闸门：loopback only、Host / Origin / Sec-Fetch-Site、CSRF（写）、字段白名单、id 只出现在路径里
（嵌套只有 `experience_ids` / `review_refs` 两处）、`CLIENT_IDENTITY_REFUSED` 拒掉
`owner` / `subject` / `artifact_id` / `workspace_id` / `grant_id`；`outcome` 只接受
`HELPED/NOT_HELPED/UNKNOWN`，`method_state` 不是可写字段。所有 ID 走
`^[A-Za-z0-9._:\-]{1,200}$`，未知查询参数 400。**没有任何删除入口。**

附加（不改旧服务）：`GET /api/phone/projects` 的行把实践文档标成
`artifact_type=review|method`、`continuable=false`；从项目屏 commit → 409
`PROJECT_IS_A_PRACTICE_DOCUMENT`。`GET /api/phone/projects/<id>` 对这两类**不发正文**：
`content=""`、`content_available=false`、`private_text_included=false`，并给出
`practice_surface` 指向实践面的路径（正文只有逐条核对证据的实践面才发得出）。

## 4. 手机界面（app.js / style.css）

新增八屏（`SCREEN_META` 均有导航标题）：

| 屏 | 内容 |
| --- | --- |
| `practice` | Memory 状态 + 显式启用 + 经历检索 + 回顾列表 + 做法列表 + 行内入口 |
| `practice-review` | 问题、head、每条经历现在的可读性、正文、写这一份 |
| `practice-review-new` | 问题 / 标题 / 选经历（只能选真实可读的） |
| `practice-review-edit` | 只改正文（CAS head 来自刚读到的那一版） |
| `practice-method` | 做法 / 条件 / 未知 / 状态 / 证据核对 / 试用记录 / 试用表单 / 修订 / 退休 |
| `practice-method-new` | 做法 + 条件 + 未知（默认"暂定，尚未验证"）+ 选经历 / 引用回顾 |
| `practice-method-revise` | 修订做法（旧版本保留、状态回 TENTATIVE） |
| `practice-experience` | 一条经历的正文、时间来源与结果 |

* 草案 / 试过 / 有反例 / 已退休用四种 chip 分开（`草案（未验证）`、`试过（仍未验证）`、
  `有反例`、`已退休`）；证据读不回来时明写"正文已停止透出"，不显示正文。
* 治理挡下时服务发来的问句 / 指令 / 范围 / 未知 / 试用说明都是空串，界面按空值自然渲染
  （显示"服务未提供"或干脆不显示那一行），不会把旧正文留在页面上；`practiceDetailView` /
  `practiceRowView` 只搬运服务给的值。
* 主屏只加一个行内入口（`实践工作台`），**不加 tile、不动 `APP_ORDER` / `HOME_ORDER`**。
* 没有删除入口；没有每轮 / 每日强制回顾。
* 草稿只在内存里（`store.practiceDraft` / `store.memoryDraft`），正文与经历不进 sessionStorage。
* 样式：`.practice-text` / `.practice-input` 按原样换行（与 `.material-text` / `.research-text` 一致）。

## 5. 实际跑过的命令与退出码

Windows（`PYTHONDONTWRITEBYTECODE=1`，`PI_SCRATCH_DIR` = 会话 scratch）：

| 命令 | 结果 | 退出码 |
| --- | --- | --- |
| `python -c "ast.parse(...)"`（三个改动/新增 Python 文件） | `AST OK` | 0 |
| `python -m unittest tests.test_phone_practice_api` | `Ran 23 tests ... OK` | 0 |
| `node --test tests/test_phone_practice_ui.mjs` | `# pass 15 / # fail 0` | 0 |
| `python -m unittest tests.test_v04_personal_practice_stage` | `Ran 32 tests ... OK (skipped=1)` | 0 |
| `python -m unittest tests.test_v04_personal_practice_stage tests.test_phone_practice_api tests.test_phone_projects_api tests.test_phone_api` | `Ran 67 tests ... OK (skipped=14)`（本轮修订前的绿组，不再重跑） | 0 |
| `python -m unittest tests.test_phone_research_api tests.test_v04_research_workspace` | `Ran 31 tests ... OK` | 0 |
| `node --test tests/test_phone_ui.mjs tests/test_phone_research_ui.mjs tests/test_phone_projects_ui.mjs` | `# pass 49 / # fail 0` | 0 |
| `python -m unittest tests.test_phone_projects_api`（`project()` 改动后复测） | `Ran 7 tests ... OK` | 0 |

治理漏洞的 red → green（父级只读核对发现，先写测试再修；只跑这两组，不重跑上面 67/49 绿组）：

| 阶段 | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| red（修前） | `python -m unittest tests.test_v04_personal_practice_stage.GovernanceLeakTests tests.test_phone_practice_api.PracticeGovernanceLeakApiTest` | `Ran 7 tests ... FAILED (failures=7)`（`data.project.content` / `question` / `instruction` / `scope` / `uncertainty` / `trials[].detail` / 旧指令 sentinel 全部命中） | 1 |
| red（修前，项目面） | 同上（新加 `test_the_project_surface_does_not_serve_a_blocked_practice_body`） | `Ran 8 tests ... FAILED (failures=1)`（`/api/phone/projects/<id>` 仍送正文） | 1 |
| green（修后） | 同上 | `Ran 8 tests ... OK` | 0 |
| green（修后，精准复测） | `python -m unittest tests.test_v04_personal_practice_stage` | `Ran 32 tests ... OK (skipped=1)` | 0 |
| green（修后，精准复测） | `python -m unittest tests.test_phone_practice_api` | `Ran 23 tests ... OK` | 0 |
| green（修后，精准复测） | `node --test tests/test_phone_practice_ui.mjs` | `# pass 15 / # fail 0` | 0 |

WSL（Ubuntu，POSIX；`PI_SCRATCH_DIR=/tmp/practice-wsl`）——真实 Supply owner（AF_UNIX）+ 真实
M0 `EvidenceStore`：

| 命令 | 结果 | 退出码 |
| --- | --- | --- |
| `python3 -m unittest tests.test_v04_personal_practice_stage` | `Ran 28 tests ... OK`（含 `RealOwnerAndMemoryIntegrationTests`；漏洞修订前的计数） | 0 |
| `python3 -m unittest tests.test_v04_personal_practice_stage.RealOwnerAndMemoryIntegrationTests` | `Ran 1 test ... OK`（漏洞修订后复跑） | 0 |

Windows 上 `skipped=1` 就是这条 POSIX 集成测试（`unittest.skipIf(os.name == "nt", ...)`）；
`tests.test_phone_api` 的 13 个 skip 是既有的 POSIX-only owner 用例，不是本轮新增。

集成测试实际验证的链路：真实 Supply owner 上 `create_review`（引用真实 Memory 经历）→
`read_review` 的 `experience_checks[0].status == CURRENT` → `create_method`（引用该回顾）→
`record_trial(HELPED)` → `method_state == TRIAL` → 列表里能读到这一份；
并断言 Supply 的 subject 与 Memory 的 owner 是两个名字也照常工作（`Supply subject ≠ Memory persona`）。

## 6. 交付边界与剩余

* 没有新增数据库 / 表 / 删除接口；退休是逻辑状态，归档沿用 owner 的 `archive_artifact`。
* 治理漏洞（父级只读核对发现，已修并回归）：`_read` 的 blocked 分支原先只清顶层
  `body`/`content`，而 `data.project.content`、`question`、`instruction`、`scope`、
  `uncertainty`、`trials[].detail`、旧指令仍是受治理内容；列表行与 `/api/phone/projects/<id>`
  也照发。现在 blocked 时这些字段全部为空（`trials` 只留 outcome/version/usable），列表行
  标 `governance_blocked` 并清空依赖字段，项目面对 review/method 不发正文。
* 回顾与做法**不执行代码**，不是 Skill 注册表；本阶段也没有"做法被自动采用"的路径（那是 C 的事）。
* `/state` 未新增 app 项：它每次连接都会跑，加进去等于每次 state 都做一遍有界扫描；
  实践 / Memory 面由各自的 GET 回答，屏内有"未连接 / 服务不可用"状态面板。
* 手机 UI 检查是**纯函数 + 源码扫描**（`node --test`，无 DOM、无浏览器）：详情 / 列表 /
  草稿校验 / 请求体 / 路径 / 屏幕表注册 / 样式都在内；真实 HTTP 与 owner 交互由
  `tests/test_phone_practice_api.py` 覆盖。没有启动真实浏览器，也没有声称做过 DOM 交互测试。
* `trials` 有界保留 12 条（先丢最旧的非反例）；更早的试用只在 owner 的版本历史里。
* 列表按 `MAX_SCAN=200` 有界扫描（一件一次 read），`truncated` 照实报。
* 未做：把回顾/做法接进 Life 主链的 prompt（C 的范围）、把 `method_ref` 写进 DecisionRecord
  （C 的范围）、`scripts/phone.py` 的 CLI 子命令（本轮不需要，手机走 HTTP）。
