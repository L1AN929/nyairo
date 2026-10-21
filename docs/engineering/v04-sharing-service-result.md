# v0.4 阶段 B 结果：分享生命周期与本地投递（`life_sharing_service`）

Status: implemented（B 侧完成并自测；A 的生活循环对接由 A 接入）

模块：`components/life/scripts/life_sharing_service.py`
测试：`tests/test_v04_sharing_service_stage.py`（内存 owner 替身 + 真实 `PrivateProjectService`）、
`tests/test_v04_sharing_supply_owner.py`（真实 Supply `ManagedArtifactStore`，sqlite 落盘，重启后仍在）
决策笔记：`.agents/notes/implemented/feature/2026-11-14-v04-life-sharing-service-local-inbox.md`

本文件 §1 是**给 A 的稳定接口**（签名/返回/状态语义/生命周期），A 只按这里对接，不要猜字段。
§2 起是实现事实、做法与未完成项。

---

## 1. 给 A 的稳定接口

### 1.1 构造

```python
from life_sharing_service import LifeSharingService, sharing_from_env

svc = LifeSharingService(projects)              # projects 就是 A 已经在用的 PrivateProjectService
svc = LifeSharingService(projects, clock=now)   # clock: 零参可调用，返回 epoch 秒 或 datetime 或 ISO 串
# 或者从环境一次建好（和自己建 PrivateProjectService 等价，不另存一份配置）
svc = sharing_from_env(env)                     # 缺 socket/subject/workspace/grant 直接抛 SharingUnavailable
```

- `projects` 必须是 **`private_project_service.PrivateProjectService` 形状**：`list_projects()` /
  `read_project(id, *, include_history=..., history_limit=...)` / `create_project(*, title, content,
  operation_key=..., proposal_id=...)` / `commit_project(*, artifact_id, content, expected_head,
  operation_key=...)`。构造时逐个 `callable` 检查，缺一个就抛 `SharingUnavailable("PROJECTS_METHODS_MISSING:...")`，
  **不会留下半个可用对象**。
- **不新开作品库、不建表、不迁移、不写人格记忆库**：每条分享都直接是现有 ManagedArtifact owner 里的
  一件 artifact（`prepare_share` 调 `projects.create_project`，之后每个状态转移调 `projects.commit_project`）。
  CAS、不可变版本、`operation_key` 幂等、UNKNOWN 的 `resolve_operation` 对账全部复用 owner 现有事实。
  本模块自己**没有任何存储**（无 sqlite、无 jsonl、无进程内账本）。
- `clock` 只影响**时间字符串与过期判定**，不参与任何写入语义。

### 1.2 四个方法（全部返回 `{ok, status, data, ...}`）

```python
svc.prepare_share(
    *,
    source_artifact_id: str,              # 必填：真实可读的来源 artifact（她的作品/经历记录）
    share_text: Optional[str] = None,     # 明确分享文本（有界，≤2000 字符）
    excerpt_chars: Optional[int] = None,  # 或者：明确要来源 head 正文的前 N 字符（1..400）
    title: Optional[str] = None,          # 可选；缺省用来源 artifact 的标题（只有标题，不带正文）
    audience: str = "local:owner",        # 固定合法受众，见 §1.4
    channel: str = "local_inbox",         # 固定本地渠道，见 §1.4
    expires_after_s: Optional[int] = None, # 可选：草稿过期窗口（60..2592000 秒）
    operation_key: Optional[str] = None,  # 可选；缺省按“意图”确定性派生
) -> dict

svc.read_share(share_id, *, include_text: bool = True) -> dict

svc.list_shares(
    *,
    states: Optional[Iterable[str]] = None,   # 只要这些状态（"DRAFT"/"DEFERRED"/...）
    include_text: bool = False,               # 列表默认不带正文
    limit: Optional[int] = None,              # 上限 MAX_LIST=100
) -> dict

svc.apply_action(
    *,
    share_id: str,
    action: str,                    # "send_local" | "defer" | "drop" | "expire"
    expected_head: str,             # 必填：草稿 CAS（read_share 返回的 head_version_id）
    operation_key: Optional[str] = None,
    defer_condition: Optional[str] = None,  # defer 必填其一
    opportunity_ref: Optional[str] = None,  # defer 必填其一（明确机会的稳定引用）
    reason: Optional[str] = None,           # drop / expire 的可选说明（有界）
) -> dict
```

`share_id` 就是**这件 ManagedArtifact 的 `artifact_id`**（`artifact:<32hex>`）：同一件东西，B 不另建
id 空间。裸十六进制 display ref 也接受（自动补 `artifact:` 前缀），和 `PrivateProjectService` 一致。

### 1.3 返回形状

所有方法：

```python
{
  "ok": bool,
  "status": str,               # 见 §1.5
  "data": {...},               # 主载荷，见下
  "source": "ManagedArtifact", # 每个事实的来源
  "share_id": "artifact:...",  # 顶层便捷别名（与 data.share_id 相同；列表/参数错误时缺省）
  "state": "DRAFT"|...,        # 顶层便捷别名（与 data.state 相同）
  "reason_code": "",           # 失败/需注意时的机器码（大写，≤64）
  "detail": "",                # 人读短说明（≤200，绝不含来源正文）
  "operation_key": "...",      # 写路径才有的幂等键
  "reconciled": bool,          # 写路径：UNKNOWN 是否已按 owner 事实对账
  "retryable": bool,           # 仅 status=="unknown"：True 才允许用同一 key 重试
}
```

**`prepare_share` 成功** `status="applied"`（同一 intent + 同一 key 重放 → owner 按 key 幂等回原结果：
`status="applied"` 且**同一个** `share_id`，不会出现第二份草稿；owner 若明确回 `NO_CHANGE` 则为
`status="already_applied"`）：

```python
data = {
  "share_id": "artifact:...", "state": "DRAFT",
  "head_version_id": "artifact-version:...",   # ← 这就是草稿版本 / 后续 CAS 的 expected_head
  "share_revision": 1, "artifact_revision": 2,
  "title": "...",
  "audience": "local:owner", "channel": "local_inbox",
  "source": {                                  # 来源绑定：只有标识与长度，没有来源正文
    "artifact_id": "artifact:...", "version_id": "artifact-version:...",
    "revision": 3, "content_chars": 812, "title_chars": 9,
  },
  "text": "<有界分享文本>", "text_chars": 120,
  "excerpt": {"kind": "EXPLICIT_TEXT"|"SOURCE_PREFIX", "chars": 120, "excerpt_chars": None},
  "expires_at": "2026-11-20T00:00:00+00:00"|None,
  "created_at": "...", "updated_at": "...",
  "private_text_included": True,
}
```

**`read_share` 成功** `status="ok"`：

```python
data = {
  "share": {                    # 该分享当前版本的全部事实
    "share_id","state","title","audience","channel",
    "head_version_id","version_number","share_revision","artifact_revision",
    "created_at","updated_at","expires_at",
    "source": {artifact_id, version_id, revision, title_chars, content_chars},
    "text","text_chars","deferred": {...}|None,"dropped": {...}|None,"expired": {...}|None,
    "state_derived": bool,      # True = 状态是由 clock 过算出来的（未落盘），见 §1.6
    "text_withheld": bool,      # True = 来源读被拒绝，依赖它的正文不投影（见 §1.9）
    "text_available": bool,     # 来源现在仍然可读
    "private_text_included": bool,
  },
  "source_current": {           # 来源现在的样子（只读一次来源，绝不把来源正文带进答案）
    "state": "READABLE"|"REVISION_CHANGED"|"ARCHIVED"|"GOVERNED"|"NOT_FOUND"|"UNAVAILABLE",
    "readable": True, "governed": False, "lifecycle": "ACTIVE",
    "head_version_id": "...", "revision": 3, "matches_recorded": True, "stale": False,
    "reason_code": "", "owner_status": "ok", "owner_reason_code": "",
  },
  "source_readable": True,      # == source_current.readable 的便捷别名
  "receipt": {...}|None,        # 本地投递记录，见 §1.7
  "delivered": bool,            # 只代表 LOCAL_DELIVERED，不代表外部渠道送达/已读
  "private_text_included": True,   # 真的没发正文就是 False（见 §1.9）
  "text_withheld": False,
  "withheld_reason_code": "",   # 例如 "SOURCE_GOVERNED"
}
```

**`list_shares` 成功** `status="ok"`：

```python
data = {
  "shares": [ {share_id,state,title,share_revision,head_version_id,updated_at,
               source:{artifact_id,version_id}, has_receipt, delivered,
               state_derived, text_chars,
               text?,                    # 只有 include_text=True 且该行来源仍可读时才有
               text_withheld, text_available, private_text_included,
               source_state, source_reason_code} ... ],   # 最近更新在前
  "counts": {"total":2,"draft":1,"deferred":0,"dropped":0,"expired":0,"local_delivered":1},
  "truncated": False,
  "ignored": [{"artifact_id","reason_code"}],   # 标题像分享、正文却不是 v1 信封的（照实列出）
  "inbox": {"channel":"local_inbox","audience":"local:owner","delivered_count":1,
            "local_only": True},                # 她的本地收件箱事实（只数 LOCAL_DELIVERED）
  "private_text_included": False,   # 真实投影：一行正文都没发出去就是 False
  "withheld_count": 0,              # 因来源读被拒绝而没投影的行数（include_text=True 时才有意义）
  "text_rows_withheld": 0,          # 同上（同一事实的别名，便于日志里直接 grep）
}
```

**`apply_action` 成功**：`status="applied"`（同一 key 重放由 owner 幂等回原结果；但**已投递的分享再发一次
仍会被拒**（`rejected` + `SHARE_ALREADY_DELIVERED`，附同一条 receipt），这不算失败 —— 投递事实始终只有一条）：

```python
data = {
  "share_id": "...", "action": "send_local", "state": "LOCAL_DELIVERED",
  "previous_state": "DRAFT",
  "head_version_id": "artifact-version:...",   # 新版本（下次 CAS 用这个）
  "share_revision": 2, "artifact_revision": 3,
  "operation_key": "share:...", "reconciled": False,
  "deferred": None,                    # defer 时 {condition, opportunity_ref, deferred_at}
  "dropped": None,                     # drop 时 {reason, dropped_at}
  "expired": None,                     # expire 时 {reason, expired_at}
  "receipt": None,                     # 仅 send_local 有，见 §1.7
  "delivery": None,                    # 仅 send_local 有：{delivery_id, delivered_at, recipient{...}}
  "delivery_count": 0,                 # 仅 send_local：这件分享的本地投递数（恒为 1，重复发送不会变 2）
}
```

### 1.4 受众与渠道（固定，不许自由填写）

```python
AUDIENCE_LOCAL_OWNER = "local:owner"    # 本实例她自己的本地收件箱（唯一允许值）
CHANNEL_LOCAL_INBOX  = "local_inbox"    # 唯一允许值
```

传别的字符串（邮箱、URL、telegram、用户名…）→ `rejected` + `AUDIENCE_REFUSED` / `CHANNEL_REFUSED`，
**不会**被当成可发送对象、也不会被写进 owner。要开新渠道是改代码的事，不是这次的事。

### 1.5 status 词表（精确）

| status | 含义 | ok |
|---|---|---|
| `ok` | 读成功 | True |
| `applied` | 这次动作真的落到 owner 新版本上 | True |
| `already_applied` | 同一个 `operation_key` 已经落地过，**没有**第二条记录/第二次投递 | True |
| `rejected` | 这个请求本身不被接受（见 `reason_code`），owner 没被写 | False |
| `stale_source` | 来源换版/归档/读不到，所以这次**不发**（要重新 `prepare_share`） | False |
| `conflict` | 草稿 CAS 失败（`expected_head` 过期），`data.current_head_version_id` 给了现在的 head | False |
| `not_found` | 没有这件分享（或 owner 读不到） | False |
| `unknown` | 效果不确定 / 对账不 conclusive，**不要盲重发** | False |
| `unavailable` | owner 不可用（socket、payload、port 错误） | False |

编程错误（参数类型不对、`expected_head` 缺失、未知 `action`、`projects` 形状不对）抛
`SharingContractRefused` / `SharingUnavailable`，**不**混进 status —— 和 `PrivateProjectService` 同一套纪律。

主要 `reason_code`：

`SHARE_TEXT_REQUIRED`（既没给 share_text 也没给 excerpt_chars，**绝不**自动拿整篇私人材料当分享文本）、
`SOURCE_NOT_FOUND`、`SOURCE_UNREADABLE`、`SOURCE_GOVERNED`（来源**读被拒绝**，见 §1.9）、
`SOURCE_ARCHIVED`、`SOURCE_REVISION_CHANGED`、
`SOURCE_HAS_NO_TEXT`、`AUDIENCE_REFUSED`、`CHANNEL_REFUSED`、`SHARE_NOT_FOUND`、`NOT_A_SHARE_ARTIFACT`、
`SHARE_ALREADY_DELIVERED`、`SHARE_TERMINAL_STATE`（DROPPED/EXPIRED）、`SHARE_EXPIRED`、
`SHARE_STATE_NOT_ALLOWED`（例如 defer 一件已投递的）、`DEFER_CONDITION_REQUIRED`、
`CAS_CONFLICT`、`OPERATION_NEVER_LANDED`（可重试）、`RESOLUTION_INCONCLUSIVE`（不可重试）、`SHARE_PORT_ERROR`。

### 1.6 生命周期（A 该怎么走）

```
（她的真实经历/作品 = source artifact，版本由 owner 记）
        │  A 明确选择“这段可以给她看” + 明确文本/明确前缀长度
        ▼
   prepare_share ──► DRAFT（private 草稿；≠ 已发出）
        ├─ defer(defer_condition=… 或 opportunity_ref=…) ──► DEFERRED
        │        │ 明确机会到了：send_local / drop（A 决定，不按 tick 改版本，不按 tick 叫模型）
        │        └─ 过期窗口到 ──► EXPIRED（由 clock 读出，见下）
        ├─ drop(reason=…) ──► DROPPED（终态，旧 timer 不复活）
        ├─ expire(reason=…) ──► EXPIRED（终态）
        └─ send_local ──► LOCAL_DELIVERED（唯一“发出”动作，见 §1.7）
```

- **DEFERRED 不消耗 LLM**：本服务没有任何 tick/轮询/定时器；`defer` 只在 A 明确调一次时写一个版本，
  “机会到了没有”由 A 自己的处境判断。`read_share`/`list_shares` 是纯读，不改变版本。
- **DROPPED / EXPIRED 是终态**：`send_local`/`defer` 再来一律 `rejected` + `SHARE_TERMINAL_STATE`
  （`expire` 语义上是 `SHARE_EXPIRED`）。没有任何“复活”入口。
- **`state_derived=True`** 只出现在一种情况：草稿带了 `expires_after_s` 且现在已过点，而这只分享
  还没被写过 EXPIRED。此时读面上照实报 `EXPIRED` + `state_derived=True`，`send_local` 直接拒绝
  （`SHARE_EXPIRED`）；要把它变成落盘事实就调一次 `expire`（A 决定，B 不替你自动写）。
- **NO_ACTION 是正常答案**：不调用 `prepare_share` 就什么都不发生，本模块零副作用。

### 1.7 本地投递的诚实语义（`send_local`）

`send_local` 写一个新版本，`state=LOCAL_DELIVERED`，并带上投递记录：

```python
receipt = {
  "delivery_id": "localdelivery:<16hex>",   # 由 operation_key 确定性派生 → 同一 key 永远是同一条投递
  "delivered_at": "2026-11-14T09:00:00+00:00",
  "channel": "local_inbox", "audience": "local:owner",
  "workspace_id": "...", "subject_id": "...",
  "recipient": "local:owner",               # 固定合法身份，不是自由邮箱/URL
  "local_only": True,
  "external_channel_sent": False,           # 没有真实外发
  "external_delivery_confirmed": False,     # 没有外部送达回执
  "read_by_audience": False,                # 沉默 ≠ 已读
  "acknowledged": False, "understood": False,
  "note": "local inbox record only; not a real external send/delivery/read",
}
```

**LOCAL_DELIVERED ≠ 外部渠道 SENT / DELIVERED / READ / ACKNOWLEDGED**：投递事实 = “这条有界文本作为
一件 artifact 版本，落在这台实例她的本地收件箱里，重启后仍可读”。`read_share` 每次都返回这条
`receipt`（可读、可追），不靠计数冒充。

`send_local` 的三道前置校验，任何一道不过都**不写**：

1. **来源**：`source_artifact_id` 现在仍可读、生命周期 ACTIVE、head 版本仍等于 `DRAFT` 里记的
   `source_version_id`。不符 → `stale_source`（`SOURCE_REVISION_CHANGED` / `SOURCE_ARCHIVED` /
   `SOURCE_NOT_FOUND` / `SOURCE_UNREADABLE`），A 要重新 `prepare_share`。
2. **草稿 CAS**：`expected_head` 必须等于当前 head（owner 自己的 CAS）。不符 → `conflict`。
3. **受众/渠道**：信封里必须是 §1.4 的固定值，否则 `rejected`（改了 owner 内容也不放行）。

`UNKNOWN`：owner 说不了结果时，`PrivateProjectService` 先用 `resolve_operation` 对账；
- 对账证明已落地 → `status="applied"`、`reconciled=True`，**不会**第二条投递；
- 对账证明没落地 → `status="unknown"`、`retryable=True`，用**同一个 operation_key** 重试即可；
- 对账也说不清 → `status="unknown"`、`retryable=False`，别动。

### 1.8 A 的最小对接示例

```python
# 1) 她刚做完一件事 / 有一段明确想分享的文本
draft = svc.prepare_share(
    source_artifact_id=artifact_id,             # 从她当前明确选择的来源来
    share_text="今天把那页留白读完了，比想象中难。",  # 明确文本（不是整篇私文）
    title="今天的留白",
)
if not draft["ok"]:
    return  # NO_ACTION 也完全合法
head = draft["data"]["head_version_id"]

# 2) 现在不想发 / 或者明确延后到某个机会
svc.apply_action(share_id=draft["data"]["share_id"], action="defer",
                 expected_head=head, opportunity_ref="evening-quiet-and-she-asks")

# 3) 以后某个明确机会（A 自己的处境判断，不是 tick）
current = svc.read_share(draft["data"]["share_id"])
if current["ok"] and current["data"]["share"]["state"] == "DEFERRED":
    sent = svc.apply_action(share_id=draft["data"]["share_id"], action="send_local",
                            expected_head=current["data"]["share"]["head_version_id"])
    sent["data"]["receipt"]   # ← 本地投递事实，不是外部已送达/已读
```

### 1.9 来源可读性 → 正文投影（读、列表、准备、发送共用同一条规则）

分享正文是**从来源 artifact 里摘出来的**，所以只要 owner 已经不肯再读那份来源，这段正文就不能再出现在
任何返回 JSON 里（一个字段都不行）。规则只有一条、四个入口共用：

| `source_current.state` | 含义 | `readable` / `governed` | `detail` 里的正文 |
|---|---|---|---|
| `READABLE` | 来源读成功，head 与草稿记录一致 | True / False | 投影（`text` 正常给出） |
| `REVISION_CHANGED` | 来源读成功，但 head 换版了（**不是**删除） | True / False | 投影，`stale=True`（发送仍被 `stale_source` 拦住） |
| `ARCHIVED` | 来源读成功，但生命周期是 ARCHIVED（`lifecycle` 照实给） | True / False | 投影，`stale=True` |
| `GOVERNED` | **读被拒绝**（`rejected`/`unknown`/`conflict`）：Governance/owner 不肯给这份来源 | False / True | **完全不投影** |
| `NOT_FOUND` | owner 明确说没有这份 artifact | False / False | 不投影 |
| `UNAVAILABLE` | owner 连不上（socket/端口/异常） | False / False | 不投影 |

- `readable=False` 的三种情况都**不再返回那段正文**：`read_share` 的 `share.text=""`、
  `share.text_withheld=True`、`share.text_available=False`；`list_shares(include_text=True)` 的
  该行**没有 `text` 键**，只有 `text_withheld=True` / `source_state` / `source_reason_code`，
  并计入 `withheld_count`。
- `private_text_included` 是**真实投影**：`read_share` 是「这次真的给了正文」，
  `list_shares` 是「返回的这些行里真的有一行带正文」，不是照抄调用参数。
- 元数据照旧：`share_id`、`state`、`head_version_id`、`share_revision`、`audience`、`channel`、
  `source`（id/version/revision/长度）、`receipt`、`deferred`/`dropped`/`expired` 全部保留 —— 被挡下的
  只是那段依赖来源的正文，不是"这份分享不存在了"。
- 拒绝**具名**：`GOVERNED` 带 owner 自己的 reason（例如 `SOURCE_GOVERNED`）；`NOT_FOUND` /
  `UNAVAILABLE` 用本服务的稳定码 `SOURCE_NOT_FOUND` / `SOURCE_UNREADABLE`，owner 的原码在
  `owner_reason_code`。**不把正常换版/归档伪称删除或治理**（见上表）。
- `prepare_share` 用同一条规则：来源读被拒绝 → `rejected` + 具名 reason + `data.governed=true`，
  一件 artifact 都不建，也不回显调用方给的 `share_text`；`send_local` 用同一条规则 → `stale_source`
  （`SOURCE_GOVERNED` / `SOURCE_REVISION_CHANGED` / `SOURCE_ARCHIVED` / `SOURCE_NOT_FOUND`）。
- 已知代价：`list_shares(include_text=True)` 现在会为每个候选行**多读一次来源**（先按标题前缀筛，
  `MAX_LIST=100` 有界）。`include_text=False` 不读来源（本来也没有正文可漏）。

---

## 2. 做法（为什么是这样）

1. **一件分享 = 一件 ManagedArtifact**。状态、来源绑定、受众、渠道、投递记录，全部编码在这件
   artifact 的**有界信封**正文里，随不可变版本一起走：

   ```
   nyairo.share.v1
   state=DRAFT
   source_artifact_id=artifact:...
   source_version_id=artifact-version:...
   source_revision=3
   audience=local:owner
   channel=local_inbox
   ...
   text:
   <有界分享文本（原样，含换行）>
   ```

   `text:` 是最后一个 header 键，之后整段都算正文。这样**版本即状态**：CAS 用 owner 的 head
   版本，历史就是状态历史，重启后仍然在（owner 的 sqlite），不需要 B 自己的库。
2. **不进第二作品库的代价也照实写**：分享件在 owner 里就是 artifact，`list_projects` /
   `list_artifacts` 会看到它。为了能便宜地筛出来，标题统一加机器前缀 `"[share] "`
   （`TITLE_PREFIX`，`is_share_title()` 可判）；真正权威仍是信封首行 `nyairo.share.v1`，
   读面上两者不符就报 `NOT_A_SHARE_ARTIFACT`，不猜。
3. **幂等键从“意图”派生**（`sharing_operation_key(...)` → `share:<32hex>`）：`prepare` 用
   (subject, workspace, source, source_version, text digest, audience, channel, title)，`send_local`
   用 (share_id, 上一版 head, channel, audience)。同一意图重放时 owner 按 key 幂等**回原结果**
   （`status="applied"`、同一个 artifact / version，不产生第二份草稿或第二条投递）；owner 若明确回
   `NO_CHANGE` 才是 `already_applied`。每次写之后都**重新读回**真实现场，`receipt` 也取自读回的
   head 而不是这次“打算写什么”。
4. **不读全量私文**：`prepare_share` 只把 `share_text`（≤2000）或来源 head 正文的**前 N 字符**
   （≤400）当分享文本；不给就 `SHARE_TEXT_REQUIRED`。来源的正文永远不进答案，只报
   `content_chars` / `title_chars` 这类长度。来源读不出来时（Governance/owner 拒绝、socket 不通）
   报 `SOURCE_UNREADABLE`/`SOURCE_NOT_FOUND`，**不会**顺手把嵌套正文带出来。
5. **不接外部 kill switch、不启任何外发**：`channel` 常量只有 `local_inbox`，没有任何 telegram /
   HTTP / SMTP 代码路径，`Production RefusingMessageProvider` 那套开关本轮完全没碰。

## 3. 怎么跑

Windows（内存 owner，23 项）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
cd D:\nyairo-rc1-body-backend
python -m unittest tests.test_v04_sharing_service_stage
python -m unittest tests.test_v04_sharing_supply_owner
```

真实 Supply owner（WSL，8 项）：`ManagedArtifactStore` 需要 `fcntl`，只能在 POSIX 上跑。

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 NYAIRO_SHARE_FIXTURE_ROOT=<scratch>/nyairo-share-b \
  python3 -m unittest tests.test_v04_sharing_supply_owner -v
```

夹具（sqlite 文件）在会话 scratch 的 `nyairo-share-b/` 下唯一创建并**保留**，不删除、不清理；每个用例一个
带 pid+时间戳的文件名，重复运行也不会撞车。

## 4. 测了什么（证据分级）

「内存 owner 替身」= 按 owner 冻结语义回答的替身（CAS、operation_key 幂等、NO_CHANGE、
UNKNOWN + 对账、ARCHIVED），测的是**本服务自己的契约**；
「真实 owner」= `components/supply/lifesupply/artifact/store.py::ManagedArtifactStore`，真 sqlite、
不可变版本、真实 operation/receipt 账本。

详见 §5（每条对应测试名）。真实 socket（`life_supply` 服务端）本轮**没有**起，Governance 预留也没走。

## 5. 已测清单（40 项）

`tests/test_v04_sharing_service_stage.py`（23 项，内存 owner 替身 + **真** `PrivateProjectService`）：

1. 没有明确 `share_text`/`excerpt_chars` → `rejected` + `SHARE_TEXT_REQUIRED`；owner 零写、零分享件，
   答案里不含来源正文。
2. 草稿绑定真实来源 head 版本与长度计数；DRAFT 不是已发出（`delivered=False`、`receipt=None`）。
3. `excerpt_chars` 有界（>400 抛 `SharingContractRefused`），取到的是前缀而非原文；
   同时给 `share_text`+`excerpt_chars` 也拒。
4. 来源不存在 → `SOURCE_NOT_FOUND`；来源已 ARCHIVED → `SOURCE_ARCHIVED`；都不落草稿、不漏正文。
5. 自由填写的受众/渠道（mailto、telegram）→ `AUDIENCE_REFUSED`/`CHANNEL_REFUSED`，零写。
6. 同一意图 + 同一 key 重放 → 同一个 `share_id`，只有一份草稿。
7. 有 `[share] ` 标题但不是 v1 信封的 artifact → `NOT_A_SHARE_ARTIFACT`，列表归入 `ignored`，正文不泄漏。
8. `defer` 无依据 → `DEFER_CONDITION_REQUIRED`（未写版本）；给了 `opportunity_ref` → DEFERRED；
   之后连续 5 次 `read_share`+`list_shares` 没有新版本、没有新写（DEFERRED 不按 tick 改状态）。
9. `drop` → DROPPED（带 reason）；再 `send_local`/`defer` 都是 `SHARE_TERMINAL_STATE`，版本与 head 不变。
10. 陈旧 head → `conflict` + `current_head_version_id`，状态与投递事实不变。
11. 来源换版（真 commit）后 `send_local` → `stale_source` + `SOURCE_REVISION_CHANGED`，未写。
12. 来源归档后 `send_local` → `stale_source` + `SOURCE_ARCHIVED`。
13. UNKNOWN 且没落地 → `unknown` + `retryable=True` + `OPERATION_NEVER_LANDED`；用同一 key 重试 → 成功，
    `delivery_count == 1`。
14. UNKNOWN 但已落地 → 对账后 `applied` + `reconciled=True`，`delivery_count == 1`，版本数 2。
15. `send_local` 写出唯一 receipt（`local_only=True`/`external_channel_sent=False`/
    `read_by_audience=False`/`acknowledged=False`）；第二次 send → `SHARE_ALREADY_DELIVERED`，同一条
    `delivery_id`，版本数不变。
16. 同一显式 key 重复发送 → 仍只有一条投递，版本数 2，`read_share` 的 receipt 时间戳未被刷新。
17. 服务重启后 `read_share` 仍 LOCAL_DELIVERED、receipt 相同、`inbox.delivered_count == 1`。
18. 列表不带正文；`counts` 分类正确；`states` 过滤生效；未知状态名抛 `SharingContractRefused`。
19. 过期窗口：`state_derived=True` 的 EXPIRED → `SHARE_EXPIRED` 拒发；`expire` 落盘后
    `state_derived=False`，再 send → `SHARE_TERMINAL_STATE`。
20. 构造缺方法 → `SharingUnavailable(PROJECTS_METHODS_MISSING:…)`；`None` → `PROJECTS_REQUIRED`。
21. 环境不全（无 workspace／无 grant／有 workspace+grant 但没 socket）→ `SharingUnavailable`。
22. `describe()` 明说 `owns_no_store=True`、`external_send=False`、只有一个受众与渠道，且不含正文。
23. owner 读不出来时**不假装空收件箱**：`list_shares` 回 `unavailable` 并给出 `failed_artifact_id`。

`tests/test_v04_sharing_supply_owner.py`（8 项，**真实** `ManagedArtifactStore` + 真 sqlite，WSL）：

24. 草稿→defer→send_local 全链成立：版本数 1→2→3；receipt 字段齐全；
    `store.lookup_operation(sent["operation_key"])` 里就是这个 share 的 `COMMIT_MARKDOWN`——幂等证据来自
    owner 自己的 operation 账本，不是本服务的计数。
25. **真重启**（同一 sqlite 重开 store + 新 projects + 新 service）→ 仍 LOCAL_DELIVERED，receipt 完全相同，
    `inbox.delivered_count == 1`，版本数 2。
26. 真 CAS：陈旧 head → `conflict`，owner 的 head 与版本数都不变。
27. 第二次 send 被拒，`delivery_id` 不变，sqlite 里版本数不变。
28. 来源换版后 send → `stale_source`，分享件版本数仍是 1。
29. drop 之后 send → `SHARE_TERMINAL_STATE`，版本数不变、`delivered=False`。
30. 同一 prepare key 两次 → 同一 artifact、只有一个分享件、只有一个版本。
31. 没有明确分享文本时，owner 里的 artifact 数完全不变。

`tests/test_v04_sharing_governed_source.py`（9 项，**定点回归**：来源读被拒绝时的正文投影；复用
`SharingTestBase` 的 owner 替身，只对 `projects.read_project` 打桩，sentinel 扫**整份 JSON**）：

32. `read_share(include_text=True)`：来源读被拒绝（`rejected` + `SOURCE_GOVERNED`）后，整份答案里
    没有 sentinel；`share.text=""`、`text_withheld=True`、`text_available=False`、
    `private_text_included=False`；`source_current.state=GOVERNED`、`governed=True`、`stale=True`、
    `owner_status="rejected"`、`owner_reason_code="SOURCE_GOVERNED"`；identity/state/head/来源绑定/
    受众/渠道/`share_revision`/`text_chars`/`source.content_chars` 一处不少。
33. `read_share(include_text=False)` 在没有来源读取时同样零正文、`private_text_included=False`。
34. `list_shares(include_text=True)`：可读时有正文且 `private_text_included=True`、`withheld_count=0`；
    来源被拒绝后整份列表 JSON 无 sentinel、`private_text_included=False`、`withheld_count=1`，
    该行没有 `text` 键、`text_withheld=True`、元数据（state/head/来源版本/长度）保留。
35. 已 `LOCAL_DELIVERED` 的分享在来源被拒绝后：receipt 原样（同一 `delivery_id`、`local_only=True`、
    `read_by_audience=False`）、`delivered=True`、但正文被扣留、`private_text_included=False`。
36. **不过度**：来源正常换版 → `source_current.state=REVISION_CHANGED`、`readable=True`、`governed=False`、
    `stale=True`、正文照常投影、`private_text_included=True`。
37. **不过度**：来源已 ARCHIVED → `lifecycle="ARCHIVED"`、`state=ARCHIVED`、`readable=True`、
    `governed=False`、正文照常投影（`stale=True`）。
38. 来源不存在（owner 明确 `not_found`）→ 正文被扣留、`state=NOT_FOUND`、`governed=False`、
    `reason_code=SOURCE_NOT_FOUND`。
39. owner 不可用（`unavailable`）→ 正文被扣留、`state=UNAVAILABLE`、`governed=False`、
    `reason_code=SOURCE_UNREADABLE`。
40. `prepare_share` 在来源读被拒绝时：`rejected` + `reason_code=SOURCE_GOVERNED` + `data.governed=true`，
    不回显调用方给的 `share_text`（sentinel 不在答案里），且一件 artifact 都不建。

## 6. 未完成 / 不能宣称

1. **没有真实外发**，也不宣称：`LOCAL_DELIVERED` 只是本地收件箱记录，`read_by_audience=False`。
2. **没有起 `life_supply` socket 的端到端链**：本轮真实 owner 用例直接构造 `ManagedArtifactStore`
   （真 sqlite、真 CAS），没走 socket、没走 Governance 授权预留；这两段由 Supply 自己的套件与父级的
   真实生活链覆盖。
3. **受众只有一个固定身份**（`local:owner`）。多对象/真实联系人受众要等 alpha contact 的
   canonical 端口真正接进生活侧时再做，本轮**不拷贝**它的影子实现。
4. **分享件会出现在 `list_projects` 里**（它就是 artifact）。要干净隐藏，A/手机侧需要把分享件
   artifact id 加进自己的 `excluded_artifact_ids`——那是 A 的写集，B 只提供 `is_share_title()`。
5. **`ManagedArtifactStore` 没有“按 artifact 列版本”的 op**：本服务不需要（状态在 head 正文里），
   但“状态变更史”只能靠 owner 侧 `artifact_versions` 直接查，B 的读面只给当前版本。
6. **AGENTS.md 的决策树/格式门禁脚本不在树里**（`scripts/` 下无对应脚本），因此只按既有 note 格式
   写 note，没有跑门禁脚本（与 `v04-private-projects-stage-result.md` §6.5 同一条事实）。

## 7. 证据（本轮真实命令与结果）

| 命令 | 结果 |
|---|---|
| `python -c "import ast; ast.parse(...)"`（两个新文件 + 两个测试文件） | `AST OK`（AST 不是 py_compile） |
| `python -m unittest tests.test_v04_sharing_service_stage`（Windows） | `Ran 23 tests … OK`，exit 0 |
| `python -m unittest tests.test_v04_sharing_supply_owner`（Windows） | `Ran 8 tests … OK (skipped=8)`，exit 0（POSIX-only，照实跳过） |
| `wsl … python3 -m unittest tests.test_v04_sharing_supply_owner -v` | `Ran 8 tests … OK`，exit 0（~20s，真 sqlite） |

真实 SQLite 证据（每个用例一个文件，全部保留在会话 scratch 的 `nyairo-share-b/` 下并能用
`sqlite3` 直接复查）：`artifact_versions` 行数（1→2→3）、`artifact_operations.operation_key`、
`artifact_effect_receipts`、`managed_artifacts.lifecycle/revision`。

### 7.1 定点修复：来源读拒绝后的正文投影（red → green）

缺陷（父级探针 `scratch/sharing_source_projection_probe.py` 复现：`detail_contains_governed_text=true`、
`list_contains_governed_text=true`、`detail_source_readable=false`）：来源 artifact 读被拒绝后，
`read_share` / `list_shares` 仍把草稿正文（内容取自那份来源）原样发出。

| 步骤 | 命令 | 结果 |
|---|---|---|
| **red**（先写回归，未改代码） | `python -m unittest tests.test_v04_sharing_governed_source` | `Ran 9 tests … FAILED (failures=4, errors=4)`，exit 1：4 个用例在 `json.dumps(answer)` 里直接抓到 sentinel（`read_share` / `list_shares` / `not_found` / `unavailable`），4 个用例因缺 `SOURCE_STATE_*` 常量而 error |
| **green**（修 `life_sharing_service.py` 后） | `python -m unittest tests.test_v04_sharing_governed_source` | `Ran 9 tests … OK`，exit 0 |
| 直接受影响的分享服务组 | `python -m unittest tests.test_v04_sharing_service_stage tests.test_v04_sharing_governed_source` | `Ran 32 tests … OK`，exit 0 |
| AST（无 py_compile） | `python -c "import ast; ast.parse(open('components/life/scripts/life_sharing_service.py',encoding='utf-8').read())"` | `AST OK` |

返回字段变化（**只增不改**）：`read_share().data` 新增 `source_readable`、`text_withheld`、
`withheld_reason_code`，`share` 新增 `text_withheld` / `text_available`；`source_current` 新增
`state` / `governed` / `owner_status` / `owner_reason_code`；`list_shares().data` 新增
`withheld_count` / `text_rows_withheld`，每行新增 `text_withheld` / `text_available` /
`private_text_included` / `source_state` / `source_reason_code`，且被拒绝的行**没有** `text` 键。
`private_text_included` 由「照抄请求参数」改为「真实投影」。`prepare_share` 的拒绝 `data` 新增
`source_state` / `governed`（reason 改为具名 `SOURCE_GOVERNED`）。
