# v0.4 阶段 A 结果：本地来访端口（`life_local_contact`）

Status: implemented（A 侧完成并自测；B 消费 pending / C 投递事件均按 §1 对接，不要猜）

模块：`components/life/scripts/life_local_contact.py`（新文件）
测试：`tests/test_v04_local_contact_port.py`（单进程契约/边界/坏文件/纯读 38 项）、
`tests/test_v04_local_contact_process.py`（真子进程并发 4 项）
决策笔记：`.agents/notes/implemented/feature/2026-11-20-v04-life-local-contact-inbox.md`

本文件 **§1 是给 B / C 的稳定接口**——签名、返回、事件状态、拒绝码、消费顺序，逐条可照抄。
§2 起是实现事实、证据与已知限制。

---

## 1. 给 B / C 的稳定接口

### 1.1 构造

```python
import life_local_contact as lc

inbox = lc.LocalContactInbox(
    state_root,          # 位置参数：**Life state root**，不是 phone root、不是 install root
    subject_id="...",    # 关键字必填：实例的 Supply subject（同一实例喂 FiniteCoPresence 的那个）
    home_ref="...",      # 关键字必填：同一实例给 FiniteCoPresence(home=...) 的那个值
    max_events=200,      # 可选，默认 200
    clock=None,          # 可选：零参可调用，返回 epoch 秒 / datetime / ISO 串
)

# 或从环境一次建好（缺身份直接抛 LocalContactError，不猜）
inbox = lc.inbox_from_environment(env, state_root=..., subject_id=..., home_ref=...)
```

- `state_root` 就是 `one_day_life.life_state_root(instance)` 返回的那个目录（`<instance>/life/state`）。
  本模块只在该目录下放两个文件：`life_local_contact.json`（账本）与 `life_local_contact.lock`
  （跨进程锁，**只在写的时候出现**）。
- `subject_id` / `home_ref` **只能来自实例**。payload 里带的 `subject_id` / `home_ref` 一律不信：
  不同的按名拒绝，相同的也被忽略并在事件上记 `client_identity_ignored=True`。
- `home_ref` 只是不透明标签（有界 ≤200 字符），本模块**不**解析它、不按它做路径。
- 构造不读盘、不建目录、不建锁。缺 `subject_id` → `CONTACT_SUBJECT_REQUIRED`；
  缺 `home_ref` → `CONTACT_HOME_REQUIRED`。

### 1.2 三个事件种类（kind）

| kind（常量） | 值 | effect | 允许记录的 outcome |
|---|---|---|---|
| `lc.KIND_MESSAGE` | `LOCAL_MESSAGE` | `OBSERVATION_ONLY` | `OBSERVED`、`UNKNOWN` |
| `lc.KIND_INVITATION` | `CO_PRESENCE_INVITATION` | `INVITATION` | `ACCEPTED`、`DECLINED`、`DEFERRED`、`UNKNOWN` |
| `lc.KIND_END` | `CO_PRESENCE_END` | `END` | `ENDED`、`UNKNOWN` |

别名：`"MESSAGE"` / `"ORDINARY_MESSAGE"` → message；`"INVITATION"` → invitation；`"END"` → end。
**`LOCAL_MESSAGE` 永远不是邀请**：即使正文写着"一起坐坐"，那也只是 `OBSERVATION_ONLY`。
C 侧要投递邀请必须显式用 `CO_PRESENCE_INVITATION` + `scope`。

两个 kind 的字符串与 `life_co_presence.KIND_INVITATION` / `KIND_END` **完全一致**，因此事件里
现成的块可以直接喂给协议（见 §1.6）。

### 1.3 `submit(payload) -> dict`

`payload` 是一份 **Mapping**，字段闭集（未知键按名拒绝）：

```
event_kind | kind        必填，"LOCAL_MESSAGE" | "CO_PRESENCE_INVITATION" | "CO_PRESENCE_END"
text | body | message    正文，三选一（>1 个 → CONTACT_BODY_AMBIGUOUS），≤2000 字符
scope                    仅 invitation 必填，≤200 字符
invitation_id            仅 end 必填（invitation 可选，缺省由 scope+时间+正文派生）
explicit_end             仅 end；与 completion_evidence_ref 至少一个
completion_evidence_ref  仅 end
reason                   仅 end，≤64 字符（缺省 EXPLICIT_END / FINITE_COMPLETION）
expires_at / duration_seconds / requires_engagement / atomic / evidence_ref   仅 invitation
occurred_at              可选 ISO；不给则用收到的时刻（不在幂等身份里）
received_at              可选 ISO（C 侧投递时刻；不在幂等身份里）
source_ref / conversation_ref / message_ref   可选结构化引用 `<prefix>:<id>`
subject_id / home_ref    可选，只用于**核对**，绝不采信
channel                  可选；只能是 "local_inbox"，其它 → CONTACT_CHANNEL_REFUSED
operation_key            可选；缺省按 payload 确定性派生 `contact_op:<24hex>`
```

返回（所有方法同构）：

```python
{
  "ok": bool, "status": str, "reason_code": str, "detail": str,
  "data": {...} | None,
  "writes_performed": 0 | 1,
  "event_ref": "contact_evt:<16hex>" | None,
  "operation_key": "contact_op:<24hex>" | "<caller 给的>",
  "replayed": bool,        # True = 这不是新事件
  "created": bool,         # True = 这次真的 append 了一条
  "reconciled": bool,
  "retryable": bool,
  "effect": "OBSERVATION_ONLY" | "INVITATION" | "END",
  "pause_requested": False,  # 恒 False —— 本模块从不请求 pause
  "activity_started": False, # 恒 False —— 本模块从不启动 Activity
  "owners_written": 0,       # 恒 0 —— submit 不写任何 owner
  "processing_state": "PENDING",
  "data": {"event": <detail，含 body>},
}
```

`status` 取值：`"applied"`（新事件）、`"already_applied"`（同 key 同 payload 的重放，回**原** event）、
`"rejected"`（按名拒绝，零写）、`"unreadable"`（账本坏了，见 §1.8）。

### 1.4 读面（全部纯读：不 mkdir、不建锁、不 bump revision、不写过期）

```python
inbox.pending(limit=None)                     # 只 PENDING（没人认领过的）
inbox.claimed(limit=None)                     # 只 CLAIMED（在飞的认领；崩溃后仍可见）
inbox.deferred_events(*, opportunity_ref=None, condition=None, limit=None)
inbox.list_events(*, states=None, kinds=None, limit=None, include_body=False)
inbox.get_event(event_ref, *, include_body=False)   # 唯一能带出正文的读面
inbox.operation(operation_key)                # 按 key 对账
inbox.summary()                               # 只有计数，无任何行、无正文
inbox.describe()                              # 自报边界（owns_activity=False 等），不读盘
```

- `pending/claimed/deferred_events/list_events` 的 `data` 都含
  `{"events": [...], "matched": int, "returned": int, "truncated": bool, "limit": int, "ledger_state": "OK"|"ABSENT"}`。
  `limit` 上限 `MAX_LIST=50`；超过按 50 算，`truncated=True` 照实报。
- **行（summary）永远不带正文**：只有 `body_chars`、`body_stored`、`body_included=False`。
  正文只在 `get_event(include_body=True)` 与 `submit` 的 `data.event` 里。
- `summary().data`：`ledger_state / event_count / capacity / remaining / counts{PENDING,CLAIMED,DEFERRED,UNKNOWN,PROCESSED} / by_kind / operation_count / bodies_stored / oldest_seq / newest_seq / revision / channel / source / subject_id / home_ref / private_text_included=False / external_channel_sent=False / delivered_to_user=False`。
- `deferred_events(opportunity_ref=..., condition=...)` 是 DEFERRED **唯一**的回归入口，
  而且它只是读——调用方仍要 `claim` + `record_result`，本模块不重新决定任何事。

### 1.5 写面：`claim` 与 `record_result`

```python
inbox.claim(event_ref, *, consumer="life_cycle", now=None, operation_key=None) -> dict
```

- 成功：`status="applied"`, `claim_token="contact_claim:<16hex>"`, 事件转 `CLAIMED`，`attempts += 1`。
- 同一 consumer 重复 claim：`status="already_applied"`，回**同一个** `claim_token`（幂等）。
- 另一 consumer 已持有：`ok=False`, `status="claimed"`, `reason_code="CONTACT_EVENT_CLAIMED"`，
  `claim` 里给出当前持有者与时刻（不抢、不等待）。
- 事件已 `PROCESSED`：`reason_code="CONTACT_EVENT_ALREADY_PROCESSED"`（终态不会被重新认领）。
- `attempts` 到 `MAX_ATTEMPTS_PER_EVENT=8`：`CONTACT_ATTEMPT_LIMIT_REACHED`。
- `consumer` 必须是 `[A-Za-z0-9_.:-]{1,80}`。

```python
inbox.record_result(
    event_ref, *, outcome,                 # 必填，闭集见 §1.2
    claim_token=None,                      # CLAIMED 状态下必填且必须匹配
    reason_code="",                        # 机器码 ^[A-Z][A-Z0-9_]{1,63}$
    detail="",                             # 人读短说明 ≤400
    session_ref=None, invitation_ref=None, decision_ref=None,
    defer_condition=None, opportunity_ref=None,
    owner_refs=None,                       # 结构化引用列表，≤8
    operation_key=None, now=None,
) -> dict
```

- `outcome="DEFERRED"` 必须给 `defer_condition` 或 `opportunity_ref`，否则
  `CONTACT_DEFER_CONDITION_REQUIRED`。给定则事件转 `DEFERRED`（**非终态**），并且
  **不再出现在 `pending()`**——下一个 tick 不会重新问模型。
- `outcome="UNKNOWN"` 必须给 `reason_code`（`CONTACT_UNKNOWN_REASON_REQUIRED`），
  事件转 `UNKNOWN` + `retryable=True`；用**同一个** `operation_key` 对账，不盲重做。
- 终态（`OBSERVED` / `ACCEPTED` / `DECLINED` / `ENDED`）→ `PROCESSED`。
  再次记录**不同**的终态 → `CONTACT_RESULT_TERMINAL`（永不复活）；
  记录**完全一样**的终态 → `status="already_applied"`（幂等）。
- 事件种类与 outcome 不匹配 → `CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY`（普通消息）
  或 `CONTACT_OUTCOME_NOT_ALLOWED_FOR_KIND`（邀请/结束）。**这是"普通来话不执行"的硬闸。**
- `outcome="ENDED"` 必须能绑定到本账本里的邀请事件：`invitation_ref`（邀请事件的 `event_ref`）
  或提交 end 时给的 `invitation_id` 已在账本里。绑定不上 → `CONTACT_END_INVITATION_REQUIRED`；
  与提交时绑定对象不一致 → `CONTACT_END_INVITATION_MISMATCH`。绑定结果落在
  `data.invitation_binding`，并写回 end 事件的 `binding`（`bound=True`）。

### 1.6 与 `life_co_presence` 的对接（B 直接用，不需要转换）

事件 detail 里有现成的两块，字段与协议 codec 逐个对齐：

```python
detail = inbox.get_event(event_ref, include_body=True)["data"]["event"]

# 邀请 → 直接喂协议
invitation = detail["invitation"]      # 11 个键：event_kind, invitation_id, source_ref,
                                       # occurred_at, expires_at, scope, subject_id,
                                       # home_ref, requires_engagement, atomic,
                                       # duration_seconds, evidence_ref
decision = cop.FiniteCoPresence(...).consider(cop.parse_invitation(invitation))

# 结束 → 直接喂协议
end = detail["end"]                    # event_kind, invitation_id, occurred_at,
                                       # explicit_end, completion_evidence_ref, reason,
                                       # evidence_ref
cop.parse_end(end)
```

`cop.classify(invitation)["effect"] == cop.EFFECT_INVITATION`、
`cop.classify(end)["kind"] == cop.KIND_END` 均已由测试固定（`CoPresenceCodecBridgeTest`）。
本模块**不 import** `life_co_presence`（B 拥有该文件），只保证字符串与字段一致。

`detail["end"]["binding"]`（在 end 事件上）：
`{"invitation_id", "invitation_event_ref", "bound", "resolved_at_submit", "reason_code", ...}`。
`resolved_at_submit=True` 表示**提交那一刻**就在本账本里找到了那条邀请事件。

### 1.7 B 的消费顺序（一次处理一条，四步）

```python
for row in inbox.pending()["data"]["events"]:          # 只 PENDING
    ref = row["event_ref"]
    got = inbox.claim(ref, consumer="life_cycle")      # ① 认领（只有一个 consumer 会赢）
    if not got["ok"]:
        continue                                       # CONTACT_EVENT_CLAIMED → 别人在处理
    token = got["claim_token"]
    detail = inbox.get_event(ref, include_body=True)["data"]["event"]
    # ② 按 detail["event_kind"] 分派：消息只观察；邀请走 Decision；结束走 end 通路
    ...
    # ③ 把结果**持久**记一次
    inbox.record_result(ref, outcome="OBSERVED", claim_token=token)          # 消息
    inbox.record_result(ref, outcome="DEFERRED", claim_token=token,
                        opportunity_ref="opportunity:...")                   # 邀请
    inbox.record_result(ref, outcome="ACCEPTED", claim_token=token,
                        session_ref="co_presence:...")                       # 邀请
    inbox.record_result(ref, outcome="ENDED", claim_token=token,
                        invitation_ref=<邀请事件的 event_ref>)                # 结束
```

- 普通消息**只能** `OBSERVED`（或 `UNKNOWN` + 原因）：`accept/defer/decline/end` 一律被拒。
  B 不得把"收到一条消息"写成接纳。
- `DEFERRED` 之后不要每个 tick 重问模型：等那个 `opportunity_ref` / `condition` 真的发生，
  再用 `deferred_events(opportunity_ref=...)` 取回、重新 `claim` + `record_result`。
- 每次 `submit` 用的 `operation_key` 都要存进 B 自己的记录：`inbox.operation(key)` 就是
  UNKNOWN 的对账读面（`status` 见 §1.8）。**不要盲重做同一个动作。**

### 1.8 `operation(key)` 的四种答案

| `status` | 含义 | 调用方该做什么 |
|---|---|---|
| `"absent"` | 这个 key 从没落地 | 可以放心用同一个 key 重试（`retryable=True`） |
| `"applied"` | 落地了；`data.event` 是事件，`data.result` 是结果（可能为 `None`） | 不要再做一次 |
| `"unknown"` | 落地了，但记录的结果是 `UNKNOWN` | 按**同一个 key** 与 owner 对账，不盲重做 |
| `"unreadable"` | 账本读不出来（带具名 reason） | 答案是"不知道"，**不是**"没落地" |

`"unreadable"` 时 `ok=False`、`status="unreadable"`、`reason_code` 为下列之一，`data=None`：

- `CONTACT_STATE_CORRUPT` —— 存在但不是 JSON / 不是对象 / 缺集合字段
- `CONTACT_STATE_EMPTY` —— 文件存在但内容为空（半写）
- `CONTACT_STATE_SCHEMA_MISMATCH` —— `schema_version` 不是 `chiyo.life.local_contact.v1`
- `CONTACT_STATE_UNREADABLE` —— 读盘本身失败
- `CONTACT_STATE_SUBJECT_MISMATCH` —— 这份账本属于另一个 subject

### 1.9 C 的投递接口（手机侧只投事件）

```python
inbox = lc.LocalContactInbox(phone_life.life_state_root(root, nested),
                             subject_id=<Phone 实例的 subject>,
                             home_ref=<同一实例给 FiniteCoPresence 的 home>)

# 普通来话（只观察）
inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": <正文>,
              "occurred_at": ..., "operation_key": <C 自己派生并保存的 key>})

# 明确邀请（scope 必填）
inbox.submit({"event_kind": "CO_PRESENCE_INVITATION", "scope": ..., "text": ...,
              "expires_at": ..., "duration_seconds": ...,
              "operation_key": ...})

# 显式结束当前邀请
inbox.submit({"event_kind": "CO_PRESENCE_END",
              "invitation_id": <该邀请的 invitation_id>, "explicit_end": True,
              "operation_key": ...})
```

C 只投递，不 `claim`、不 `record_result`（那是 B 的写集）。投完立刻用
`inbox.get_event(ref)` 读回真实 `processing_state`：`PENDING` 就是"还没被处理"，
**不能**在 UI 上显示成"她接受了"。`Life` 没在跑就是 `PENDING`/`queued`，照实显示。

---

## 2. 实现事实

### 2.1 存储

一份 JSON：`<state_root>/life_local_contact.json`

```json
{"schema_version": "chiyo.life.local_contact.v1", "subject_id": "...", "home_ref": "...",
 "revision": 11, "seq": 3, "created_at": "...", "updated_at": "...",
 "events": {"contact_evt:...": {...}}, "operations": {"contact_op:...": {...}},
 "order": ["contact_evt:...", ...]}
```

- 原子写：唯一临时名 → flush → `fsync` → `os.replace` → 目录 `fsync`。
  崩溃只可能留下旧文件或新文件，不会留半截被当成"空账本"。
- 跨进程锁：**独立**文件 `life_local_contact.lock`（因为账本每次都被 replace，
  锁在被 replace 的 inode 上会静默失效）。POSIX `flock(LOCK_EX)`；Windows
  `msvcrt.locking(LK_NBLCK)` 带超时。**只有写路径进锁**，读路径连 lock 文件都不碰
  （有专门用例：读一整圈后目录哈希不变、目录里没有 `.lock`）。

### 2.2 幂等与身份

- `payload_digest` = 规范化事件体的 sha256；`occurred_at`/`received_at` **只在调用方真的
  给了**时才进 digest。于是"客户端原样重发"是同一意图（同一 key），而"两条内容相同的
  真消息"各有自己的 `seq` 与 `event_ref`，都保留。
- `operation_key` 缺省 = `contact_op:<sha256(digest)[:24]>`。同 key 同 payload → 原事件；
  同 key 不同 payload → `CONTACT_OPERATION_KEY_CONFLICT`（**不覆盖**）。
- `event_ref` = `contact_evt:<sha256(key|digest|seq)[:16]>`；`seq` 单调递增，重启后从
  `doc["seq"]` 继续。

### 2.3 事件状态机（transport 账本，不是 Activity 状态）

```
PENDING ──claim──> CLAIMED ──record(终态)──> PROCESSED   (终态，不可再 claim / 不再改)
                      │
                      ├──record(DEFERRED + 具名条件)──> DEFERRED ──claim──> CLAIMED …
                      └──record(UNKNOWN + reason)─────> UNKNOWN  ──claim──> CLAIMED …
```

`ACCEPTED` 是**transport 账本里的终态结果**，不是 Activity 成功：真正起 joint Activity 的
仍是 B 的 `FiniteCoPresence.begin`（Decision + Adoption）。本模块不写 Activity、不写 pause、
不写 checkpoint。

### 2.4 边界（为什么这不是第二个 owner）

- 无关系库、无 trust、无亲密分、无聊天域：`describe()` 自报
  `owns_relationship=False, owns_conversation=False`。
- 无 Activity 影子状态：事件里只有 `effect/processing_state/outcome`，`pause_requested` 与
  `activity_started` 恒为 `False`；结果字段白名单 `ALLOWED_RESULT_FIELDS` 里**没有任何**
  Activity 字段。
- 无外发：只有 `channel=local_inbox`、`source=local_operator`，
  `external_channel_sent=False`、`delivered_to_user=False`、`local_only=True`。
  这不是"用户收到了"。
- 无模型：本文件不 import 任何模型客户端，也不发起网络。

---

## 3. 验证

### 3.1 命令与实际退出码

| 命令 | 平台 | 退出码 | 结果 |
|---|---|---|---|
| `py -3.13 -B -m unittest -q tests.test_v04_local_contact_port` | Windows 3.13.15 | **0** | `Ran 38 tests … OK` |
| `py -3.13 -B -m unittest -q tests.test_v04_local_contact_process` | Windows（`msvcrt` 锁路径） | **0** | `Ran 4 tests … OK` |
| `py -3.13 -B -m unittest -q tests.test_v04_local_contact_port tests.test_v04_local_contact_process` | Windows | **0** | `Ran 42 tests … OK` |
| `python3 -B -m unittest -q tests.test_v04_local_contact_port tests.test_v04_local_contact_process`（WSL Ubuntu, Python 3.12.3） | WSL（`flock` 锁路径） | **0** | `Ran 42 tests … OK` |

`PYTHONDONTWRITEBYTECODE=1` 全程设置；语法检查用 `ast.parse`（未用 `py_compile`）。

### 3.2 覆盖（每条都有具体用例）

- 落盘重开：`StorageAndReopenTest`（ref/body/operation key 重启后照样读得回）。
- 幂等：`test_same_intent_is_one_event`、`test_parallel_replay_of_one_client_key_is_one_event`（4 个真进程）。
- 同 key 换 payload：`test_same_key_with_another_payload_is_refused_not_overwritten`。
- 跨进程并发 append 不丢：`test_parallel_appends_lose_nothing`（4 进程 × 6 条，`seq` 连续 1..24）。
- 并发 claim 只有一个赢：`test_only_one_process_can_claim`（真进程）。
- 固定身份：`IdentityTest`（异 subject / 异 home 按名拒绝；同值也记 `client_identity_ignored`）。
- 明确邀请 vs 普通文字：`MessageVersusInvitationTest`（消息带 scope → 拒绝；
  消息记 `ACCEPTED` → `CONTACT_MESSAGE_CANNOT_AFFECT_ACTIVITY`）。
- codec 对齐：`CoPresenceCodecBridgeTest`（真 `parse_invitation` / `parse_end` / `classify`）。
- defer 具名 / 终态不复活：`DeferAndTerminalTest`、`DeferralClaimVisibilityTest`。
- GET 零写：`test_reads_write_nothing_at_all`（整目录 sha256 前后一致）、
  `test_a_read_on_an_absent_ledger_creates_no_files`、`LockFileTest`。
- 坏文件具名：`CorruptStateTest`（垃圾 JSON / 空文件 / 换 schema / 换 subject / 缺集合），
  且拒绝后**原文件一字未改**。

### 3.3 未覆盖 / 明确不做

- 真实模型、真实 socket、真实 Phone HTTP：不在 A 写集，由 B/C/父级各自覆盖。
- 事件过期只做**读面推导**（`expired: bool`），本模块**没有**任何定时器，也不写过期：
  多久以后不再算"还有效"由 B 的处境判断和 LR-3/LR-4 决定。
- `MAX_EVENTS=200` 满了就按名拒绝（`CONTACT_INBOX_CAPACITY_REACHED`），**不**淘汰最旧那条。
  压缩/归档属于将来另开的决策（见笔记"Consequences"）。

---

## 4. 给 B / C 的硬约束清单（照抄即可）

1. 构造必须传**实例**的 `subject_id` 与 `home_ref`；C 不许把客户端 uid / 路径变成其中任何一个。
2. C 只 `submit`；`claim` / `record_result` 是 B 的。
3. B 处理一条 = `claim` → 读详情 → 分派 → `record_result`（一次）。不要跳过 `claim`。
4. 普通消息**只能** `OBSERVED`/`UNKNOWN`。想让"我来啦"变成共处 = 让 C 投递 `CO_PRESENCE_INVITATION`。
5. `DEFER` 必须带 `defer_condition` 或 `opportunity_ref`；之后靠 `deferred_events(...)` 回来。
6. `ENDED` 必须带 `invitation_ref`（邀请事件的 `event_ref`）或提交 end 时的 `invitation_id`。
7. UNKNOWN 一律先 `inbox.operation(key)` 对账，再决定是否用**同一个** key 重试。
8. 不要把 `ACCEPTED`（transport 结果）当成 Activity 已建立；Activity 只由 B 的 owner 通路产生。
9. 不要把 `state_root` 指向 phone root / install root / 任何别的目录。
10. 列表/摘要里不会出现正文；C 想在 UI 显示正文必须显式 `get_event(include_body=True)`，
    并且只在明确详情视图里。
