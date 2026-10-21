# v0.4 阶段 A 结果：共同实践服务（共享材料 / 双方贡献 / 作品正文 / 会话状态）

Status: implemented；A 服务自己的用例已跑通（内存 owner 契约 44 项 exit0；真实 Supply
`ManagedArtifactStore` owner 10 项 exit0，WSL 真 sqlite）。B/C 按第 1 节接口只读消费，不猜 kwargs。

写集：`components/life/scripts/shared_practice_service.py`（新）、
`tests/test_v04_shared_practice_service.py`、
`tests/test_v04_shared_practice_service_supply.py`（新）、本文件、own note。
未改：`private_project_service.py`、`life_sharing_service.py`、
`life_local_contact.py`/`life_co_presence.py`/`autonomous_life_*`/`agency_*`（B 核心）、
`phone/**`（C）、`MANIFEST.json`、`scripts/fix_platform_world_move.py`、`scripts/fix_weixin_world_move.py`。

定位：一段共同实践**就是**一件既有 `ManagedArtifact`（经 `PrivateProjectService` → `LifeArtifactPort`）。
没有第二作品库、没有聊天数据库、没有第二 Activity/关系/Memory owner。A **不**决定要不要相处、
不写 Activity、不写主体意愿（那是 B 的 owner），只负责这件作品的材料引用、贡献记录、正文版本与
`OPEN/PAUSED/ENDED` 状态。

---

## 1. 给 B/C 的精确接口（先发布这一节）

模块：`components/life/scripts/shared_practice_service.py`
（`components/life/scripts` 已在 `sys.path` 里时 `import shared_practice_service as sps` 即可）。

### 1.1 答复信封（所有公开方法一致）

```text
{
  "ok": bool,                 # 只有 reads 与 applied/already_applied 写入为 True
  "status": str,              # "ok" | "applied" | "already_applied" | "conflict"
                              # | "unknown" | "not_found" | "unavailable" | "rejected"
  "source": "ManagedArtifact",# owner 名，不是服务自称
  "data": {...},              # 见 1.5 / 1.6 / 1.7
  "reason_code": str,         # 拒绝时才有；稳定，B/C 按它分支，不要匹配中文 detail
  "detail": str               # 可选，≤200 字，只用于人读
}
```

`status` 取值来自 `private_project_service.STATUS_*`（`pps.STATUS_OK/APPLIED/ALREADY_APPLIED/
CONFLICT/UNKNOWN/NOT_FOUND/UNAVAILABLE/REJECTED`）。`ok=True` 时 `reason_code` 不出现。
`unknown` 一定带 `data["retryable"]`（bool），并且 owner 已经对账过：服务**不会**自己重发写。

编程错误（调用方乱传结构、没有 owner）才抛异常：
`SharedPracticeContractRefused`（契约）、`SharedPracticeUnavailable`（没接 owner）。

### 1.2 类与构造

```python
sps.SharedPracticeService(
    projects,                       # 必填：位置参数，PrivateProjectService 实例（鸭子类型）
    *,
    actor,                          # 必填："life" | "user"；没有默认角色
    source_verifier=None,           # 读面：get_decision/get_event/get_action/get_adoption_by_id
    subject_id=None,                # 缺省取 projects.subject_id
    clock=None,                     # () -> float | datetime | str；测试确定性用
    max_list=sps.MAX_LIST,          # 50
    max_scan=sps.MAX_SCAN,          # 200
)
sps.CALLER_LIFE == "life"           # 主体侧：可写她的那一半，但必须带经核对的来源
sps.CALLER_USER == "user"           # 用户/手机侧：只能写用户自己的话
```

`projects` 必须提供 `list_projects / read_project / create_project / commit_project / close_project`，
缺一个就抛 `SharedPracticeUnavailable("PROJECT_METHODS_MISSING:...")`。

**factory（按既有 env 绑定构造，半接好的环境返回 None，不造半可用实例）**

```python
sps.shared_practice_service_from_env(
    port=None,          # LifeArtifactPort / 端口对象 / socket path str；None 时按 env 建
    env=None,           # dict；None = os.environ
    *,
    actor,              # 必填
    source_verifier=None,
) -> Optional[SharedPracticeService]
# 别名：from_env / service_from_env / shared_practice_from_env
# env 名沿用 private_project_service：workspace / grant / subject / socket / excluded
# （ENV_WORKSPACE_NAMES / ENV_GRANT_NAMES / ENV_SUBJECT_NAMES / ENV_SOCKET_NAMES /
#   ENV_EXCLUDED_NAMES）。workspace/grant/subject 任一缺失 → None。
# 不抛异常：owner 不可用也返回 None。
```

### 1.3 角色与写权限（`sps.ACTOR_WRITES`，越界是具名拒绝不是异常）

| 操作 action | life | user(phone) | 说明 |
|---|---|---|---|
| `open` | ✅ | ✅ | 开一段只是**候选**，不是接纳 |
| `user_contribution` | ❌ | ✅ | 只有用户自己的话；Life 不能替用户说 |
| `subject_response` | ✅ | ❌ | 她最终可见的回应；**必须** basis |
| `subject_contribution` | ✅ | ❌ | 她真正加进作品的一段；**必须** basis |
| `work_text` | ✅ | ❌ | 作品正文换版；**必须** basis |
| `pause` / `end` | ✅ | ✅ | 状态闸门，不删任何内容 |
| `resume` / `reopen` | ✅ | ❌ | 她的意愿，**必须** basis |
| `reconfirm` | ✅ | ✅ | 材料换版后重指引用 |

`ACTOR_BASIS_REQUIRED = ("subject_response","subject_contribution","work_text","resume","reopen")`。
越界 → `ok=False, status="rejected", reason_code="SHARED_PRACTICE_ACTOR_NOT_ALLOWED"`
（`data` 里带 `action/actor/allowed_actors`）。**phone 永远不能写主体内容、不能直接标 ACCEPTED。**

### 1.4 来源核对（`basis`）——只认 owner 读面，不信调用方声称

```python
basis = {"decision_ref": "..."}   # 四选一，且只允许一个 key：
#  decision_ref -> kind "decision", 读面 get_decision,      身份键 ("decision_id",)
#  event_ref    -> kind "event",    读面 get_event,         身份键 ("event_ref","event_id")
#  action_ref   -> kind "action",   读面 get_action,        身份键 ("action_id",)
#  adoption_ref -> kind "adoption", 读面 get_adoption_by_id, 身份键 ("adoption_id",)
```

核对规则：读面返回 `None` → `SOURCE_NOT_FOUND`；返回 `{"ok": False, ...}` 或抛异常 →
`SOURCE_UNREADABLE`；答复里没有该来源的身份、或身份与被点名的 ref 不同 → `SOURCE_MISMATCH`；
读面没接 / 缺方法 → `SOURCE_VERIFIER_UNBOUND`；没给 basis → `SOURCE_REQUIRED`；
**user 侧带 basis** → `SOURCE_NOT_ALLOWED_FOR_CALLER`。核对通过后落盘的 `basis` 只有
`{"kind","ref","verified":true,"verified_id","owner_status","verified_at"}`，owner 的正文/理由不进版本。

### 1.5 方法签名（全部 keyword-only 参数照抄即可）

```python
describe() -> dict                      # 自身接线，不调 owner
now() -> str                            # ISO 即时（clock 注入）

list_practices(*, include_ended=True, limit=None) -> dict
read_practice(session_id, *, include_history=False, include_material_excerpt=False,
              entry_limit=12, excerpt_chars=240) -> dict
context_view(session_id, *, include_material_excerpt=False,
             entry_limit=12, excerpt_chars=240) -> dict

open_practice(*, title, opening_text, material_refs, operation_key=None, basis=None) -> dict
submit_user_contribution(session_id, *, text, expected_head,
                        commitment_state="NONE", operation_key=None) -> dict
submit_subject_response(session_id, *, text, basis=None, expected_head,
                        commitment_state="NONE", operation_key=None) -> dict
submit_subject_contribution(session_id, *, text, basis=None, expected_head,
                            commitment_state="NONE", operation_key=None) -> dict
commit_work_text(session_id, *, text, basis=None, expected_head, operation_key=None) -> dict
pause_practice(session_id, *, note="", expected_head, basis=None, operation_key=None) -> dict
resume_practice(session_id, *, note="", expected_head, basis=None, operation_key=None) -> dict
end_practice(session_id, *, reason="", expected_head, basis=None, operation_key=None) -> dict
reopen_practice(session_id, *, reason="", expected_head, basis=None, operation_key=None) -> dict
reconfirm_materials(session_id, *, expected_head, material_refs=(), basis=None,
                    operation_key=None) -> dict
```

* `expected_head` **必填**：就是上一次读到的 `data.project.head_version_id`（CAS 前置条件）。
  空/非串在本地就抛 `SharedPracticeContractRefused`，不惊动 owner。
* `basis=None` 在需要来源的写上是**具名拒绝** `SHARED_PRACTICE_SOURCE_REQUIRED`（不是 TypeError）。
* `operation_key` 是幂等键（**重要**）：同一意图调用两次 → 第二次 `ok=True,
  status="already_applied"`、`data.replayed=True, data.new_version=False`、不写第二版；同一个 key
  换内容 → `reason_code="SHARED_PRACTICE_OPERATION_KEY_REUSED"`（服务本地具名拒绝，owner 的 request
  digest 是第二道）。不给就按（动作、artifact、head、序号、正文）派生——**派生键不保证跨调用幂等，
  B/C 要幂等请显式传 key**。`open_practice` 目前不做服务级去重（owner 的 create replay 语义见 §2）。

### 1.6 状态 / 联合状态 / 提交承诺 / 材料状态

```python
STATES = ("OPEN", "PAUSED", "ENDED")                  # 只是这件作品的状态，不是 Activity
JOINT_STATES = ("CANDIDATE", "JOINT", "ENDED")
#  CANDIDATE：还没有经核对的她的回应（打开≠接纳，用户贡献≠接纳）
#  JOINT：存在 verified 的 subject response
#  ENDED：当前状态 ENDED
COMMITMENTS = ("NONE", "PENDING", "ACCEPTED", "DECLINED")
USER_COMMITMENTS = ("NONE", "PENDING")                # 用户最多挂 PENDING
MATERIAL_STATES = ("READABLE","MOVED","ARCHIVED","GOVERNED","NOT_FOUND","UNAVAILABLE")
BLOCKING_MATERIAL_STATES = ("ARCHIVED","GOVERNED","NOT_FOUND","UNAVAILABLE")
STALE_MATERIAL_STATES = ("MOVED",)
```

状态闸门：`ENDED` 上再写 → `ALREADY_ENDED`（正文与贡献都还在）；`PAUSED` 上写 →
`STATE_NOT_ALLOWED`；`resume` 非 PAUSED → `NOT_PAUSED`；`reopen` 非 ENDED → `NOT_ENDED`；
`end` 已在 ENDED → `ok=True, status="already_applied"`（不写第二版）。
`reopen` 之后 `data.ended` 为 `None`（当前不在结束态），`data.last_ended_at` 保留那个时刻，
结束块本身仍留在 owner 的旧版本里。

### 1.7 `data` 结构（B/C 读这些键，不要解析标记行）

**`open_practice`**：`session_id, title, state="OPEN", joint_state="CANDIDATE", practice_revision,
materials[], text_chars, accepted=False, note` + owner 回执
（`artifact_id, version_id, version_number, revision, lifecycle, operation_key, effect_state,
owner_status, reconciled`）。

**`submit_*` / `commit_work_text`**：
`session_id, state, joint_state, entry{seq, contributor, kind, text, chars, commitment_state, basis,
operation_key, recorded_at, against_version_id}, contribution_count, response_count,
practice_revision, accepted`（`commit_work_text` 另有 `text_chars, basis`，无 `entry`）+ owner 回执
同上 + `replayed, new_version`（见 §1.5 幂等）；重放时另有 `head_version_id`（它所在的那一版）。

**`pause/resume/end/reopen`**：`session_id, state, previous_state, practice_revision`
（`reopen` 另有 `reopen_count, text_chars, contribution_count, note`；带 basis 时回显核对块）。

**`read_practice`**：
```text
session_id, title, state, joint_state, accepted, practice_revision, created_at, updated_at,
project{artifact_id,title,lifecycle,status,revision,head_version_id,version_number,
        created_at,updated_at,workspace_id,source},   # 不含原始 content（标记行永不外泄）
materials[{artifact_id,version_id,recorded_revision,recorded_at,state,readable,blocking,stale,
           reason_code,owner_status,lifecycle,current_version_id,current_revision,title,
           content_chars,(excerpt 仅显式要求且 READABLE)}],
material_count,
work{text,chars,available,updated_at,updated_by,basis},   # text = 真实正文（不含标记行）
contributions[], contribution_count, contributions_truncated,
responses[], response_count, pending_commitments[],
paused{at,by,text,basis}|None, ended{at,by,text,basis}|None, last_ended_at,
reopen_count,
governance{blocked,stale,reason_code,redacted[],writes_allowed,material_count},
private_text_included, skipped_entries,
history{complete,reason_code,entries[{version_id,version_number,parent_version_id,created_at,
        operation_key,content_chars,is_head}],count,source}   # 仅 include_history=True
```

**`context_view`**（决策/屏幕用的文本优先视图，零写）：
`session_id, title, state, joint_state, practice_revision, text, text_chars, text_available,
head_version_id, materials[], contributions[], contribution_count, contributions_truncated,
responses[-3:], last_response, pending_commitments[], paused, ended, last_ended_at, reopen_count,
governance{}, limits{entry_limit,contributions,text_chars},
not_authority{activity:False,session_willingness:False,relationship:False,conversation:False,note},
private_text_included`。
**`text` 是正文本身**（真实内容），任何答复都不含 `nyairo-shared-practice:v1` 标记行。

**`list_practices`**：`sessions[{session_id,title,state,joint_state,practice_revision,created_at,
updated_at,head_version_id,version_number,revision,lifecycle,materials[],material_count,
contribution_count,response_count,pending_commitments,governance_blocked,governance_reason,
title_withheld}], count, matched, truncated, scanned, skipped{other,ended,unreadable},
private_text_included=False`。`skipped.other` = 读得到但不是这一段（普通项目工件/坏标记），
`skipped.unreadable` = owner 读不出来；`skipped.ended` = 被 `include_ended=False` 滤掉的已结束段。

### 1.8 材料治理（fail closed，全有或全无）

每次读都经过 owner 重读材料（不信记录里的版本）。`ARCHIVED/GOVERNED/NOT_FOUND/UNAVAILABLE`
→ 该段的**每个派生文本**按构造扣留（`title/work.text/contributions[].text/responses[].text/
paused.text/ended.text/materials[].title/excerpt/project.title`），`governance.blocked=true`、
`governance.redacted` 点名扣了什么；身份/状态/版本事实仍可读。`MOVED`（材料换版）→ 正文仍可读、
`stale=true`、**新写被拒**（`SHARED_PRACTICE_MATERIAL_STALE`），唯一出口是
`reconfirm_materials`。开段时材料读不出来 → `SHARED_PRACTICE_MATERIAL_UNREADABLE`，一段都不建。

### 1.9 上限

`MAX_OPENING_CHARS=1200, MAX_WORK_CHARS=12000, MAX_CONTRIBUTION_CHARS=1200,
MAX_RESPONSE_CHARS=2000, MAX_MATERIALS=6, MAX_CONTRIBUTIONS=24, MAX_RESPONSES=12,
MAX_LIST=50, MAX_SCAN=200, MAX_REF_CHARS=200, DEFAULT_ENTRY_LIMIT=12, DEFAULT_EXCERPT_CHARS=240,
MAX_TOTAL_CONTENT_CHARS=min(pps.MAX_CONTENT_CHARS-1000, 19000)`。

### 1.10 跨进程读（C 的 GET、父级核对用，不用构造服务）

```python
sps.is_shared_practice_content(content) -> bool     # 第一行是本服务标记且 schema/state 可读
sps.shared_practice_state(content) -> str           # "OPEN"|"PAUSED"|"ENDED"|""
sps.parse_shared_practice_content(content) -> dict  # 永不抛；坏标记给 parse_error
sps.build_shared_practice_content(metadata, body="") -> str
sps.shared_practice_operation_key(*parts) -> str    # 确定性幂等键（"shared-practice:<digest>"）
# 标记：<!-- nyairo-shared-practice:v1 {json} --> 只认第一行；正文逐字返回。
# parse 的 parse_error 取值：MARKER_JSON_INVALID / MARKER_NOT_AN_OBJECT /
#   MARKER_SCHEMA_UNKNOWN:<schema>；schema 固定 "nyairo.shared-practice.v1"。
```

### 1.11 拒绝码（稳定常量，B/C 按常量分支）

```text
SHARED_PRACTICE_NOT_A_SESSION / _MARKER_INVALID / _ACTOR_NOT_ALLOWED / _STATE_NOT_ALLOWED
SHARED_PRACTICE_ALREADY_ENDED / _NOT_ENDED / _NOT_PAUSED
SHARED_PRACTICE_MATERIAL_REQUIRED / _MATERIAL_UNREADABLE / _MATERIAL_UNAVAILABLE / _MATERIAL_STALE
SHARED_PRACTICE_SOURCE_REQUIRED / _SOURCE_NOT_ALLOWED_FOR_CALLER / _SOURCE_VERIFIER_UNBOUND
SHARED_PRACTICE_SOURCE_NOT_FOUND / _SOURCE_UNREADABLE / _SOURCE_MISMATCH / _SOURCE_GOVERNED
SHARED_PRACTICE_AGREEMENT_NOT_USERS
SHARED_PRACTICE_OPERATION_KEY_REUSED
SHARED_PRACTICE_OPERATION_NOT_FOUND / _OPERATION_UNKNOWN
SHARED_PRACTICE_OPERATION_UNAVAILABLE / _OPERATION_UNSUPPORTED
SHARED_PRACTICE_TEXT_REQUIRED / _CONTENT_TOO_LARGE / _TOO_MANY_ENTRIES
SHARED_PRACTICE_PORT_ERROR / _OWNER_UNAVAILABLE
OPERATION_NEVER_LANDED / RESOLUTION_INCONCLUSIVE
```

### 1.12 只读原 key 对账面：`operation(operation_key)`（新增，C 可直接替换 UNSUPPORTED 分支）

```python
operation(operation_key) -> dict      # 纯读：零 create / commit / archive，**绝不重发写**
```

按 owner 自己的 receipt / operation 记录回答“这个键做了什么”，**不建第二份账本**。查询面按顺序
探测（`describe()["operation_lookup"]` 报当前用的是哪一层）：
`projects.operation` → `projects.resolve_operation` → `projects.port.read_effect_receipt` →
`projects.port.lookup_operation` → `projects.port.resolve_operation`；都没有就具名
`SHARED_PRACTICE_OPERATION_UNSUPPORTED`，不会退化成“自己记一份”。

`data` 字段：
```text
operation_key   str
effect          "APPLIED" | "NOT_LANDED" | "UNKNOWN" | "UNAVAILABLE"
applied         bool                  # effect == APPLIED
recorded        bool                  # 同 applied：owner 账上有这一条
owner_reason    str                   # owner 自己的 reason，原样不翻译
artifact_id     str|null              # APPLIED 时是哪件作品
version_id      str|null
version_number  int|null
surface         str                   # 这一层答复来自哪个读面（见上）
read_only       True
resend          False                 # 明确：不要用这个键重发写
session_id      str                   # 仅当落地工件确实是本服务的一段
shared_practice bool                  # 落地工件是不是一段共同实践
state / joint_state / practice_revision   # 同上，仅 shared_practice 时
```

`ok` / `status` / `reason_code`（与仓库既有 `operation()` 面一致：applied / not_found /
unknown / unavailable）：

| effect | ok | status | reason_code |
|---|---|---|---|
| `APPLIED` | true | `applied` | （无） |
| `NOT_LANDED` | false | `not_found` | `SHARED_PRACTICE_OPERATION_NOT_FOUND` |
| `UNKNOWN` | false | `unknown` | `SHARED_PRACTICE_OPERATION_UNKNOWN`（`data.retryable=false`） |
| `UNAVAILABLE`（owner 没这个面） | false | `unavailable` | `SHARED_PRACTICE_OPERATION_UNSUPPORTED` |
| `UNAVAILABLE`（面在、读失败） | false | `unavailable` | `SHARED_PRACTICE_OPERATION_UNAVAILABLE` |

`operation_key` 为空/非串 → 抛 `SharedPracticeContractRefused`（契约错误，不是 owner 答复）。
APPLIED 且落地工件是本服务的一段时，额外给 `session_id/state/joint_state/practice_revision`；
不是（别人用同一命名空间的键）则 `shared_practice=false`，其余字段照样给，不猜。
**它永远不写、不重试、不换键。**

### 1.13 `open_practice` 的同 key 幂等（新增）

调用方**显式**传 `operation_key` 时，先走 §1.12 再决定写不写：

- `APPLIED` 且落地工件是本服务的一段、且文档里的 `open_operation`（开场时写下的 key+意图，
  **不会被后续写入覆盖**；意图 = `title` + `opening_text` + `material_refs`，**不含任何时间**）
  与这次一致 → `ok=true, status="already_applied"`，`data.session_id` 是原会话，
  `replayed=true, new_version=false, operation_check="APPLIED"`，并给出该段当前的
  `state/joint_state/materials/practice_revision/contribution_count/response_count/accepted`；
  **不新建第二件 artifact，也不会 CONFLICT**。
- `APPLIED` 但意图不同（或落地工件不是本服务的一段）→ `SHARED_PRACTICE_OPERATION_KEY_REUSED`，
  一件不写。
- `UNKNOWN` → 原样返回 unknown（`resend=false`），一件不写；先确认，不换键重试。
- `NOT_LANDED` → 正常创建（owner 明确说这个键没落过）。
- `UNAVAILABLE`（owner 没有对账面）→ 照常创建一次，但答复带
  `operation_check="UNAVAILABLE", idempotency_unverified=true` 与说明：这种 owner 上 A 无法证明
  这个键没落过，**不保证跨服务重建去重**。真实 Supply 端口有 `resolve_operation`，不走这条。
- 没传 `operation_key` → `operation_check="SKIPPED"`：每次派生一个**新**键（含实例序号与即时），
  所以两次相同开场就是两段（A 不把“没给键”当成幂等契约）；`data.operation_key` 回显这次真正用的键。

`data.operation_check` 取值：`"APPLIED" | "NOT_LANDED" | "UNAVAILABLE" | "SKIPPED"`。
`describe()` 另报 `operation_lookup`（当前探测到的面，空串 = 没有）与 `operation_effects`。

---

## 2. 已知 owner 缺口（报父级，A 不自建）

1. **版本史只能拿到 head**：`LifeArtifactPort` 没有 `list_versions`，`ManagedArtifactStore` 有
   `read_version` 但端口不暴露。`private_project_service._history` 因此对真实端口只回 head
   一条（`complete=false, reason_code=PROJECT_PORT_LACKS_LIST_VERSIONS`）。可读的“可延续”仍成立
   （head 正文 + owner 不可变版本行 + `version_number/parent_version_id`），但**逐版正文列表**要等
   owner 补端口能力。A 不伪造历史，也不在 A 侧建第二份版本表。
2. 没有“共同实践专用”类型/投影：一段共同实践就是普通 `ManagedArtifact`；靠第一行标记 + `title`
   区分。若 Supply 以后加类型/投影，A 只需改标记解析，不动 owner。
3. **没有任何对账面的 owner**（既无 receipt/operation 读取、create 也不按键去重）：A 无法证明某个
   键没落过，只能照常写一次并在答复里标 `operation_check="UNAVAILABLE",
   idempotency_unverified=true`——这种 owner 上跨重建去重**不成立**，是具名限制而不是静默分叉。
   真实 Supply 端口有 `read_effect_receipt`/`lookup_operation`/`resolve_operation`，不走这条。
4. 本轮之前写下的会话文档里没有 `open_operation` 记录，所以用旧 key 重开一段旧会话会得到
   `SHARED_PRACTICE_OPERATION_KEY_REUSED`（A 不能证明当初的意图就是这次）。这是刻意保守：
   宁可让调用方换 key 或改用 `read/list` 找回来，也不把两段不同来源的作品并成一段。

## 3. 验证（实际跑过的，含 exit / skip / 限制）

| 用例集 | 环境 | 结果 |
|---|---|---|
| `tests/test_v04_shared_practice_service.py`（内存 owner 契约，含对账面与开场重放） | Windows / py3.13 / `PYTHONDONTWRITEBYTECODE=1` | **59 tests, exit 0** |
| `tests/test_v04_shared_practice_service_supply.py`（真实 `ManagedArtifactStore`） | WSL / py3.12 / 真 sqlite | **14 tests, errors 0 failures 0 skipped 0, exit 0**（Windows 上整文件 skip：store 需要 `fcntl`） |
| `tests/test_v04_sharing_supply_owner.py`（我复用的真实 port/fixture，回归） | WSL / py3.12 | 8 tests, exit 0 |
| `tests/test_v04_shared_practice_life_seam.py`（B 消费 A 的 seam，回归） | Windows / py3.13 | 29 tests, exit 0（B 本轮仍在写，数字取我实际跑的那一刻） |
| `tests/test_v04_shared_practice_phone.py`（C 消费 A） | Windows / py3.13 | 两次观察：①25 tests / 16 failures，全部是 HTTP 层 `404 NOT_FOUND`（答复里没有 A 的 `source` 字段，C 那几分钟正在改 `server.py`/`practice_shared.py` 的路由）；②C 稳住后最后一次 **16 tests / exit 0**。两个数字都取自我实际跑的那一刻；我没有改 `components/phone/**` |
| `tests/test_v04_private_projects_stage.py`（同 owner 的相邻套件） | Windows / py3.13 | 15 tests，1 failure：`LifeOwnerAdapterTest.test_gateway_creates_one_project_through_the_owner` 断言 `detail["artifact_id"] == "artifact:" + artifact_id`，实际拿到 `artifact:artifact:<hex>`（观察事实；父级未有等价 baseline，我不判定归属，也未改该文件与 `life_artifact_port.py`） |

真实 Supply 用例覆盖（每条都读真 sqlite 的 `artifact_versions` / `artifact_operations` /
`artifact_effect_receipts`）：双方各贡献并落真实正文（打开 + 4 次写 = 5 个不可变版本）、同 key 同
payload 复用（本地 `already_applied`，版本数不变）、同 key 异 payload 具名拒绝、真 CAS 陈旧 head
冲突、UNKNOWN“其实落盘了”只用 receipt 对账且只多一个版本、材料换版（`MOVED` 正文仍可读 + 新写
被拒 + reconfirm 后恢复）、材料归档（`blocked`，title/正文/贡献/回应全部扣留，5 个 sentinel 一个
不出现，仍可正当结束）、end→reopen 保留正文与贡献且不重复、纯读（list/read/context/operation）前后
`artifact_operations` 与版本数完全不变、context 带真实正文且不含标记行。

本轮新增的真实 Supply 回归（开场幂等与对账面）：

* `test_an_open_retry_with_the_same_key_is_one_session`：同一 key + 同一意图，**真重启**（新
  `ManagedArtifactStore`/port/projects/service，同一个 sqlite 文件）后重试 → `already_applied`、
  同一 `session_id`、`version_id` 不变、`managed_artifacts`/`artifact_versions`/`artifact_operations`
  三个计数都不增（零写）。
* `test_a_lost_open_answer_reconciles_and_a_rebuilt_service_finds_it`：create 的答复在传输里丢了，
  工件与 v1 真的落地 → A 用 owner receipt 对账成 `applied/reconciled`；重启后再用同一 key 打开 →
  `already_applied`，仍然只有一件工件、一个版本。
* `test_an_open_with_another_intent_on_the_same_key_is_refused`：换标题 / 换开场 / 换材料都是
  `SHARED_PRACTICE_OPERATION_KEY_REUSED`，工件数不变；同一批材料重复点名不算换意图（仍交回原段）。
* `test_the_operation_surface_reads_owner_receipts_and_writes_nothing`：`operation("user:op")` 给出
  `APPLIED` + 真 `artifact_id`/`version_id` + 段身份；`operation("key:never-used")` 给出
  `not_found/NOT_LANDED`；两次查询前后 operations 与两个工件的版本数完全不变。

限制（照实写）：
* 残留（我造成的，未删，因为本轮禁止删除）：早先几次 WSL 运行没有带 `PYTHONDONTWRITEBYTECODE=1`，
  在 `components/life/scripts/__pycache__/`、`components/supply/lifesupply/*/__pycache__/`、
  `tests/__pycache__/` 下留下了 cpython-312 的 `.pyc`（`shared_practice_service`、
  `private_project_service`、`life_artifact_port`、`life_sharing_service`、`lifesupply.*`、
  `test_v04_sharing_supply_owner`、`test_v04_shared_practice_service_supply`）。它们被
  `.gitignore` 的 `__pycache__/` 覆盖，不在 `git status` 里；后来所有 Python 运行都带
  `PYTHONDONTWRITEBYTECODE=1`。清单已列在这里，删除动作留给父级。

* 真实 Supply 用例只在 POSIX（WSL）跑；Windows 上是 skip，不是通过。
* 真实模型/真实实例/真实 socket+Governance 都没有参与（A 不联网、不起实例）；真实产品验证归父级。
* `include_history=True` 的真实端口只回 head（§2 缺口 1），用例用带 `list_versions` 的内存替身
  验证“多版本仍存”的那一半，不把内存结果冒充真端口能力。
* 笔记门禁实际跑过（脚本在 `~/.agents/skills/write-notes-like-deepseek/scripts`，不在仓库里）：
  `node --experimental-strip-types verify-agent-note-tree.ts` → **exit 0**（79 篇 tree 与相对链接通过，
  我自己那篇先因两个坏链接被它抓住，已修）；`verify-agent-note-format.ts` → **exit 1**，唯一报错是
  `implemented/feature/2026-10-20-v04-shared-practice-life-continuation.md`（B 正在写的笔记，标题行/
  Status 行不合规）；我不改别人正在写的笔记，只如实记录。
* 夹具只创建不删除：`$NYAIRO_SHARED_PRACTICE_FIXTURE_ROOT` 或 `/tmp/nyairo-shared-practice-a`
  下每次一个新 sqlite 文件；没有 `TemporaryDirectory`、没有 rmtree。`SupplyPracticeFixture` 可直接
  被父级复用（一个 store、两个调用方角色、一件真实材料）。
