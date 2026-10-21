# v0.4 阶段 B 结果：私人多项目 owner 服务、提案器与手机工作台

Status: implemented（B 侧完成并自测；A 侧主循环集成由 A 接入）

本文件先给 **A 直接可调用的稳定接口**（签名/返回值/状态语义都在这里），再写手机面、怎么跑、还剩什么。
设计取舍与备选方案见 `.agents/notes/implemented/feature/2026-10-27-v04-private-projects-owner-and-phone.md`。

---

## 1. 给 A 的稳定接口

三个入口，任选其一即可；都**不写第二作品库、不建表、不迁移**。

### 1.1 直接持有服务对象（推荐：A 已经知道 workspace/grant/port）

```python
from private_project_service import PrivateProjectService

svc = PrivateProjectService(
    port,                                  # 任何有 list_artifacts/read_artifact/create_artifact/
                                           # commit_version/archive_artifact 的对象（LifeArtifactPort）
    workspace_id,                          # 实例的 workspace_id
    grant_id,                              # COMMIT_MANAGED_ARTIFACT 的 grant_id
    excluded_artifact_ids=(                # 非项目：阅读材料 + 私人事项文档（不传就都算项目）
        reading_artifact_id, private_items_artifact_id,
    ),
)
```

构造时若 port 缺上面任一方法 → `ProjectUnavailable("PORT_METHODS_MISSING:...")`；缺
workspace/grant/subject → `ProjectContractRefused`。**不会留下半可用对象。**

### 1.2 五个方法（读 3 + 写 3，签名与返回）

```python
svc.list_projects(*, include_archived=True, limit=None) -> dict
svc.read_project(artifact_id, *, include_history=True, history_limit=50) -> dict
svc.read_project_history(artifact_id, *, limit=50) -> dict
svc.create_project(*, title, content, operation_key=None, proposal_id=None) -> dict
svc.commit_project(*, artifact_id, content, expected_head, operation_key=None) -> dict
svc.close_project(*, artifact_id, expected_revision, operation_key=None) -> dict
```

**读**（`list_projects`）：

```python
{
  "ok": True, "status": "ok", "source": "ManagedArtifact",
  "subject_id": "...", "workspace_id": "...",
  "projects": [                                    # 最近更新的在前
    {"artifact_id": "artifact:<hex>", "title": "...",
     "lifecycle": "ACTIVE"|"ARCHIVED", "status": "active"|"archived",
     "revision": 3, "head_version_id": "version:...", "version_number": None,
     "created_at": 1760000000.0, "updated_at": 1760000100.0,
     "workspace_id": "...", "source": "ManagedArtifact"},
  ],
  "counts": {"total": 2, "active": 1, "archived": 1, "excluded": 2},
  "excluded_artifact_ids": [...], "truncated": False,
  "private_text_included": False,                  # 列表永远不带正文
}
```

**读**（`read_project`，唯一带正文的读）：

```python
{"ok": True, "status": "ok", "artifact_id": "...", "workspace_id": "...",
 "project": {<上面那一行 + "content": "...", "content_chars": 12,
             "content_available": True, "version_number": 3,
             "parent_version_id": "..."|None>},
 "history": {"complete": False, "reason_code": "PORT_LACKS_LIST_VERSIONS",
             "entries": [{"version_id": "...", "version_number": 3,
                          "parent_version_id": "...", "created_at": 1760000100.0,
                          "operation_key": "...", "content_chars": 12, "is_head": True}],
             "count": 1, "source": "ManagedArtifact"},
 "private_text_included": True}
```

**写**（三个方法同一个返回形状）：

```python
{"ok": True, "status": "applied"|"already_applied",
 "source": "ManagedArtifact", "operation_key": "project:<...>",
 "artifact_id": "artifact:<hex>", "version_id": "...", "version_number": 3,
 "head_version_id": "...", "parent_version_id": "...",
 "lifecycle": "ARCHIVED"|None, "revision": 4,
 "effect_state": "APPLIED"|"NO_CHANGE"|"CONFLICT"|"REFUSED"|"NOT_FOUND"|"UNKNOWN",
 "owner_status": "OK"|..., "reason_code": "", "detail": "",
 "reconciled": False}
```

失败时 `ok=False` 且 `status` 为 `conflict` / `not_found` / `rejected` / `unknown` / `unavailable`
之一（**不抛异常**，只有编程错误才抛 `ProjectContractRefused`）。`unknown` 额外带
`retryable`：`True` = owner 明确说没落地，可用**同一个 operation_key** 重试；
`False` = 结果无法确认，别动。

### 1.3 用 A 自己的 `ProjectOwnerGateway`（`life_project_options` 已写好的那条缝）

`life_project_options.resolve_gateway()` 会依次找模块上的
`private_projects_from_env / service_from_env / from_env / private_project_service_from_env`。
本模块四个名字都提供了，所以 A 不用改代码就能接上：

```python
import life_project_options as lpo
gw = lpo.resolve_gateway(environment={                      # 也接受显式传模块/对象
    "NYAIRO_LIFE_SUPPLY_SOCKET": "/path/supply.sock",       # 兼容 CHIYO_/LIFE_SUPPLY_ 名字
    "NYAIRO_LIFE_SUBJECT": "chiyo",
    "NYAIRO_LIFE_WORKSPACE_ID": "...",
    "NYAIRO_ARTIFACT_GRANT": "...",
    "NYAIRO_LIFE_NON_PROJECT_ARTIFACTS": "artifact:reading,artifact:items",   # 逗号分隔
})
gw.available()                                             # True/False
gw.list_projects()                                         # [{"artifact_id", "title", "lifecycle", ...}]
gw.create_project(title=..., seed_text=..., source_refs=[...], option_ref=...)
```

这是 `LifeProjectOwner`（与本服务同一个对象），返回 A 文档里那套**大写** `status`：
`OK` / `REFUSED`(带 `reason`) / `CONFLICT` / `UNAVAILABLE` / `UNKNOWN`。
环境不全时工厂直接抛，A 的 gateway 就答 `PRIVATE_PROJECT_OWNER_UNAVAILABLE`（不造假）。
`workspace_id` / `subject_id` 与本服务不一致 → `REFUSED: WORKSPACE_MISMATCH` / `SUBJECT_MISMATCH`。

⚠️ **A 侧的已知细节**：`life_project_options._artifact_id_of` 会把 `artifact:` 前缀剥掉再交给
A 的调用者。本服务已兼容：**裸十六进制 id 会被规范回 `artifact:<hex>`**（只认 8–64 位十六进制），
所以 A 拿到的 display ref 也能直接回查。要拿"真正"的 id 仍以 `list_projects()` 的
`artifact_id` 为准。

### 1.4 A 需要做的一件事（唯一的跨写集改动）

版本历史目前只有 head 一条。请给 `LifeArtifactPort` 加一个**可选**方法：

```python
def list_versions(self, artifact_id: str) -> list[dict[str, Any]]:
    """ops: list_versions（新 op，READ_OPS）；映射 Supply 的 artifact_versions 行：
    {version_id, version_number, parent_version_id, created_at, operation_key, content}"""
```

只要求「能被 `getattr(port, 'list_versions')` 拿到且可调用」。B 侧已按这个契约写好：
一旦 port 有这个能力，`read_project(...)["history"]["complete"]` 自动变 `True` 并列出全部版本
（新的在前），B 不需要再改。Supply 侧的 `ManagedArtifactStore.read_version(version_id)` 已存在
（`components/supply/lifesupply/artifact/store.py:274`），缺的只是「按 artifact 列版本」的 op。

---

## 2. 提案器（`private_project_cognition`）

纯结构、0 副作用、不 import 服务、不持 port。

```python
bounded_seed(*, materials=None, questions=None, existing_projects=None) -> dict
structural_proposals(seed, *, limit=3) -> list[dict]        # 0-LLM，只从 seed 的事实长
model_proposals(client, seed, *, limit=3, timeout_s=25.0, max_output_tokens=700) -> list[dict]
form_proposals(seed, *, client=None, limit=3, structural_fallback=True) -> dict
select_proposal(proposals, proposal_id) -> dict             # 这一步就是"主体明确选择"
chosen_project_payload(proposal) -> {"title", "content", "proposal_id", "kind", "source_refs"}
```

提案行：`{proposal_id: "pproposal:<16hex>", kind: QUESTION_FROM_MATERIAL|MATERIAL_NEXT_PAGE|
OPEN_DIRECTION, title, first_step, seed_text, source_refs[≤3], origin: STRUCTURAL|MODEL,
requires_selection: True}`。`form_proposals` 返回
`{schema_version, origin, proposals, model_error, requires_selection, creates_nothing}`。
模型报错 → `ProjectProposalError(reason_code)`；`form_proposals` 会降级为结构提案并在
`model_error` 里写明原因（**调用方永远看得见这不是模型给的**）。

**提案不能创建项目**：唯一路径是 `select_proposal` → `chosen_project_payload` →
`svc.create_project(**payload, operation_key=...)`；`create_project` 本身只收字符串标题+正文。

`materials` 的形状就用 `subject_private_materials.read_reading_situation(...)` 的结果
（`state/title/pages/position.page/source_id`），`questions` 是她的 `unclear`/`memo`
（`{"id", "page", "text"}`）。

---

## 3. 手机面（给父级验收看）

`/state` 新增一个 app：`projects`（`set(apps) == set(APPS)` 仍成立）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/phone/projects` | 列表；不含正文；`private_text_included: false`；**零写** |
| GET | `/api/phone/projects/<artifact_id>` | 正文 + head + 版本历史；`private_text_included: true` |
| POST | `/api/phone/projects` | `{title, content, operation_key}` → `data.artifact_id/version_id/version_number` |
| POST | `/api/phone/projects/<id>/commits` | `{content, expected_head, operation_key}` → 下一版（CAS） |
| POST | `/api/phone/projects/<id>/close` | `{expected_revision, operation_key}` → `lifecycle: ARCHIVED` |

- GET 走宿主/来源闸门且**不写任何东西**（测试里断言 owner 侧零写）；正文只出现在详情这一条私人面上。
- 写要 `X-CSRF-Token`；请求体白名单，`artifact_id`/`workspace_id`/`grant_id` 出现在 body 里是
  `CLIENT_IDENTITY_REFUSED`（409）；每次写都落 `phone/operations.jsonl`
  （`project_create` / `project_commit` / `project_close`）。
- 状态映射到既有信封：`applied`→200、`already_applied`→200、`conflict`→409、`not_found`→404、
  `unknown`→202、`unavailable`→503。

界面（`components/phone/web/{app.js,style.css}`）：

- 主屏新增行内入口 **「私人项目」**（与「实体日用店」「房间与布置」同一排，**不加 tile、不动
  `APP_ORDER`/`HOME_ORDER`**）。
- `projects` 屏：列表（标题/状态/版本/时间）、进行中与已归档计数、非项目工件数、「新建项目」。
- `project` 屏：正文（`pre-wrap`，原样换行）+ 状态/rev/head + 版本历史；历史不全时明说
  「只有当前版本是确定的 …等 port 接上 list_versions 就能列全」；动作：修改正文 / 归档（确认弹层）/ 刷新。
- `project-edit` 屏：新建（标题+正文）或修改（标题只读、正文可改）；**没有 head 就不发**，
  保存后回到详情读回服务的新 head。
- 界面是**观察与调试入口**，不是自主生活的前置：她不点这些按钮时，A 的主循环照样用 §1.1 的接口推进。

---

## 4. 怎么跑（一次就够）

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
cd D:\nyairo-rc1-body-backend
python -m unittest tests.test_v04_private_projects_stage tests.test_phone_projects_api   # 22 项
node --test tests/test_phone_projects_ui.mjs                                            # 6 项
node --check components/phone/web/app.js
```

夹具根在会话 scratch（`$env:PI_SCRATCH_DIR\nyairo-projects-pi`）下唯一创建并**保留**，不删除、不清理。

---

## 5. 已测什么（22 + 6）

服务层（`tests/test_v04_private_projects_stage.py`，15 项，内存 owner 替身）：

1. 列表排除材料/PDI 且旧 artifact 照旧可见（含 counts、不含正文）；
2. 详情带正文/head/`version_number`，历史 `complete=False` + `PORT_LACKS_LIST_VERSIONS` 且只有 head；
3. 直接 `read_project` 一件阅读材料 → `not_found` + `PROJECT_ARTIFACT_EXCLUDED`；
4. port 具备 `list_versions` 时 `complete=True`、新→旧排序、`is_head` 正确；
5. 创建必须显式标题+正文（空串、非字符串、提案对象都拒），同 `operation_key` 重放不产生第二件；
6. 提交必须带 `expected_head`（`None` → 拒），陈旧 head → `conflict`，正常提交 → v2 + revision 2；
7. 归档 → `ARCHIVED`、正文与 rev 保留、再归档同键 `already_applied`、`include_archived=False` 时隐藏；
8. UNKNOWN 先 `resolve_operation`：已落地 → `applied`+`reconciled`；明确没落地 → `unknown`+`retryable`
   且 revision 不变；
9. 提案：有界、`requires_selection`、重复标题被丢弃、`select_proposal` 拒绝空/未知 id；
10. 模型失败 → `ProjectProposalError`；`form_proposals` 降级并写 `model_error`；好模型输出 → `ORIGIN_MODEL`；
11. A 的 `ProjectOwnerGateway` 经适配器建出真项目、重复 option 幂等、`workspace` 不符被拒；
12. 环境不全的工厂直接抛（A 那边就是 `PRIVATE_PROJECT_OWNER_UNAVAILABLE`）。

HTTP（`tests/test_phone_projects_api.py`，7 项，真实 loopback + 内存 owner）：

13. `GET /projects` 200、无正文、counts、零写；14. `GET /projects/<id>` 正文/head/历史不全；
15. 材料 GET → 404 + `PROJECT_ARTIFACT_EXCLUDED`；
16. `POST /projects`：缺 CSRF 403、未知字段 400、body 带 `artifact_id` 409、正常 200 + 流水；
17. `POST commits`：缺 head 400、陈旧 head 409、正常 200 v2 且详情已更新；
18. `POST close`：200 applied、再 close `already_applied`、正文仍在、只出现 archive；
19. 非法 `expected_revision`（0/-1/true/"1"/1.5）→ 400 且 owner 一次都没被写。

界面数据层（`tests/test_phone_projects_ui.mjs`，6 项）：列表/详情/历史视图、
草稿校验、三个请求体（缺 head 不发）、路径拼装（怪 id 拼不出来）、入口注册 + `.project-text` 样式 +
`APP_ORDER` 未变。

顺带复跑（未被本轮改动破坏）：`tests.test_phone_api` + `tests.test_phone_reading_api`（54 项 OK）、
`tests/test_phone_ui.mjs`（34）、`tests/test_phone_world_ui.mjs`（25）。

---

## 6. 剩余具体问题

1. **版本历史只有 head**：等 A 按 §1.4 给 port 加 `list_versions`；B 侧无需改动。
2. **项目不能改名、不能删除**：owner 没有 rename / 真删除动词（`tombstone` 会隐藏内容，B 不暴露）。
   要改名只能新建一份 + 归档旧的，界面已把标题在编辑态设为只读并注明。
3. **A 的 `_artifact_id_of` 会剥前缀**：已用「裸十六进制 → `artifact:<hex>`」兼容，但 A 若要长期持有
   id，建议以 `list_projects()` 的 `artifact_id` 为准（A 侧改动，属 A 写集）。
4. **没有真实 Supply socket 的端到端链**：本轮的 22 项跑在内存 owner 上（真 owner 的 Governance 预留、
   不可变版本由 Supply 自己的套件与父级的真实生活链覆盖）。真实链需要 A 的自主循环 + 实例里的真 socket。
5. **AGENTS.md 提到的「决策树与格式校验脚本」不在树里**（`scripts/` 下没有对应脚本），因此只按
   `.agents/notes/implemented/feature/2026-10-27-v04-private-projects-owner-and-phone.md` 的既有格式写了 note，
   没有跑门禁脚本。
6. **`docs/engineering/v04-projects-phone-api.md` 不存在**：本轮 HTTP 面的权威描述就在本文件 §3
   （`server.py` 模块 docstring 已同步这段契约）。若父级要单独契约文件，可从 §3 直接摘。
