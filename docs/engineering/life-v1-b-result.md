# B 包回执：私人日常内容与房间物件（`private_daily_items`）

- 候选树：`D:\nyairo-v03-backend`，分支 `work/v03-w01-navigation`，HEAD
  `7f09d26df64e054faf51ba35ad85c64f73a5a837`。**未 reset / 未 checkout / 未 clean / 未
  stage / 未 commit / 未 push**；HEAD 未移动，旧 UNCOMMITTED 全部原样保留。
- 本单**只新增**自己写集里的文件，全部仍是 **UNCOMMITTED（untracked）**：
  `components/life/scripts/private_daily_items.py`、
  `tests/test_private_daily_items.py`、本回执、`private-daily-items-usage.md`。
- `life_artifact_port.py` **一字未改**（`git status` 里没有它）：现有
  `create_artifact` / `commit_markdown` / `read_artifact` / `read_head` / `resolve_operation`
  已经够用，所以没有加"最小窄命令"。若 D 需要新命令，接口说明见 §4。
- 没碰：cycle / assembly / resident / one_day_life、`test_one_day_life*`、A 的 Body/处境/评价、
  C 的连续性/Memory、Supply 数据库与 owner schema。没跑整套 Supply 回归，也没跑全仓。

## 1. 交付了什么

`private_daily_items.py` 是**一份 typed 私人事项文档**的编解码 + 命令 + 投影适配器：整份文档
存进**现有** ManagedArtifact 的**一个** artifact，每次修改提交一个**新版本**，读写全部走已经
存在的 `LifeArtifactPort`（同一个 owner、同一个 Unix socket、同一套 op）。

| 语义 | API | 关键不变量 |
|---|---|---|
| 备忘 | `add_memo` / `revise_memo` | 正文是私有文本，`metadata_view` 默认不给 |
| 收藏 | `save_reference` | `read=False`、`content_fetched=False`；**不取全文、不置已读**；`full_text` 对收藏直接拒绝 |
| 来源与版本 | `record_source_acquisition` | 必须带 `receipt_ref` 与**非空** `obtained`（页码/范围证据）；可带 ≤480 字的 `excerpt`（超长拒绝，不静默截断） |
| 阅读位置 | `set_reading_position` | 必须已有该 source+revision 的**可用**取得记录；`understanding` 恒为 `UNKNOWN` |
| 主观未懂 | `mark_unclear` | 与阅读位置**分属两条记录**，各自带自己的 note |
| 收件 | `receive_item` / `mark_read` | 字段只来自明确接收记录；`co_presence` 恒 `False`；置已读必须给 `evidence_ref` |
| 撤权/失效 | `mark_source_unavailable` | 之后正文/摘要一律 `PrivateItemsSourceUnavailable`，文档里没有缓存能复活它 |
| 旧版本书签 | 派生 | 同一来源出现更新的取得记录后，旧 revision 的位置 `needs_reread=True`（`SOURCE_REVISION_ADVANCED`），且**不被静默改指到新版本** |
| 隐私 | `metadata_view` / `items` / `snapshot` | 字段**白名单**；正文/摘要/未懂 note/locator/excerpt 一律不出；`private_text_included=False`；要文本只能显式 `full_text` / `disclosure_excerpt` |
| 房间物件 | `room_object_refs` / `note_item_at_observed_object` | 只接受**实际观察到**的 object ref；明确标成"观察链接"，**没有**任何 placement 实现 |

存储形态：文档 = 紧凑 JSON（`sort_keys` + 固定分隔符，字节稳定），写进 artifact 的 content。
这是**v1 原型协议**，不冒称原本已有 Phone owner；没有新建数据库、表或 owner。

## 2. 真实 owner 证据（不是 fake）

owner 是**真的**：`lifesupply.service.life_supply.LifeSupplyService` + `LifeSupplySocketServer`
跑在自己的 Unix socket 上，Governance / Workspace / ManagedArtifact 是**同一个 canonical
SQLite**，落在唯一夹具根里。断言读的是 owner 自己给出的事实（head `version_id`/`version_number`、
`read_effect_receipt`、`lookup_operation`、grant `revocation_state`）。

| 验收项 | 真实证据 |
|---|---|
| 真实版本更新 | 每次写都让 head `version_number` +1，`read_artifact` 读回真实内容（不是只比 hash） |
| 重开可读 | `OwnerServices.restart()`：关掉 owner、`unlink` 自己的 socket、用**同一数据根**起新 owner 实例；新 port + 新 adapter 读回全部条目（备忘正文、位置、未懂、收藏、未读计数） |
| CAS 冲突不覆盖 | 注入一次**真实交错**：A 计划对 head v2 提交，B 的真实提交先落盘成 v3，A 的提交带 `expected_head=v2` 到达 → owner 返回 `CONFLICT stale expected_head` → 适配器抛 `PrivateItemsConflict`；head 仍是 v3，A 的便签**不在** owner content 里，B 的两张都在 |
| 撤权拒绝 | 用 owner 的 operator op `revoke_grant`（`get_grant` 取真实 revision）撤掉 artifact grant；同一 grant 的 `commit_markdown` 被 Governance 拒（`reason=GOVERNANCE_REFUSED`，status `REVOKED`）→ `PrivateItemsOwnerRefused`，head 不变 |
| 同 operation UNKNOWN 不盲重发 | 见下 |
| 显式绑定 | 作品 artifact（markdown）绑上去 → `DOCUMENT_NOT_JSON`；另一个 JSON artifact 但 `doc_type` 不同 → `NOT_A_PRIVATE_DAILY_ITEMS_DOCUMENT`；私人事项写入**不改变**作品版本（`version_number` 前后一致），全文里没有私人便签 |

### UNKNOWN 两项（注入分级：只有这两步是注入的传输答复）

真实 owner 之上把**某一个 op 的答复**改写成 `UNKNOWN`，其余请求原样转发：

1. `forward=True`（**写入真的落盘**，只是答复丢了）：适配器调 `resolve_operation` →
   `read_effect_receipt` 命中 → 返回 `applied=True, reconciled=True, detail=RECONCILED_AFTER_UNKNOWN`，
   并重读 head 刷新缓存；owner 侧版本正好 +1，effect receipt 的 `artifact_id` 与绑定一致。**没有第二次提交。**
2. `forward=False`（**连写入都没发生**）：`read_effect_receipt` + `lookup_operation` 都是
   `NOT_FOUND` → 返回 `applied=False, retryable=True, detail=UNKNOWN_RESOLVED_NEVER_LANDED`，
   版本不变，owner 里查不到该 operation。随后用**同一个 operation_key + 同一份内容**重试 → 真实落盘、
   版本只 +1、条目只有一条；再用同一个 key 配**不同内容** → owner 明确 `CONFLICT`，版本仍只 +1。
   即：一次意图最多一个版本，适配器自己**不重发**。

### 证据等级（照写）

- **真实 owner 证据**：上面全部，除下面两类。
- **注入的传输答复**：只有 §UNKNOWN 两项（测试里由 `_InjectedLostAnswer` 标出）。
- **合成输入**：只有房间物件一项——World observation 是调用方给的一份最小映射，形状取自
  `world_living_skeleton.observe_world` 的 `local_environment.furniture`。**本单没有启动 World
  owner**，也不声称这份观察来自真实世界运行；没有 World 集成证据。

## 3. 测试命令与实际结果

```
bash /mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37/life-v1-b/run_private_daily_items.sh
```

脚本内容：`PYTHONDONTWRITEBYTECODE=1 <w04venv python> -m unittest tests.test_private_daily_items -v`。
本单实际跑了 4 次（含修完两个真缺陷后的复跑），每次 **14 项全过**：

```
Ran 14 tests in 13.232s
OK
TEST_EXIT=0
```

逐项（`-v` 输出）：`test_real_versions_and_reopen_read_back_the_same_items`、
`test_cas_conflict_does_not_overwrite_the_newer_version`、
`test_revoked_grant_refuses_the_write_and_leaves_the_document_alone`、
`test_unknown_that_actually_landed_is_reconciled_by_receipt`、
`test_unknown_that_never_landed_is_retryable_with_the_same_key`、
`test_saved_reference_never_fetches_text_and_never_marks_read`、
`test_page_number_is_not_understanding_and_unclear_is_a_separate_item`、
`test_old_source_revision_bookmark_needs_reread_and_is_not_repointed`、
`test_metadata_does_not_leak_private_text`、`test_unavailable_source_is_refused_not_revived`、
`test_inbox_needs_an_explicit_receipt_and_read_needs_evidence`、
`test_explicit_binding_never_picks_the_work_artifact`、`test_room_object_refs_are_observation_only`、
`test_contract_refusals_are_local_and_send_nothing`。全部 `ok`。

夹具根：`/tmp/nyaw05r.jdK3q/b-private-items-*`（唯一 `mkdtemp` 创建，**保留不清理**；可用
`NYAIRO_LIFE_V1_B_ROOT` 覆盖）。本单累计 70 个唯一根。不 `rm -rf`、不重建固定路径、不动共享目录、
不删任何用户数据。`state` 根与 A/C 的夹具互不相交（独立前缀）。

跑测试时写的字节码：只有一次 `py_compile` 在
`components/life/scripts/__pycache__/`（本就存在、已被 `.gitignore` 忽略）留下过一个
`private_daily_items.cpython-312.pyc`，**已删除**；正式跑测用 `PYTHONDONTWRITEBYTECODE=1`，没有
新增其他字节码。`git diff --check` 对自有文件（`--no-index` 方式，因为都是 untracked）无空白错误。

## 4. 给 D 的接口简表（接线用）

```python
import life_artifact_port as lap
import private_daily_items as pdi

port = lap.LifeArtifactPort(supply_socket, subject_id)        # 现有 port，未改

# 1) 建文档 artifact（走 owner 自己的 create_artifact；首版就是空文档，1 个版本）
artifact_id, outcome = pdi.create_document_artifact(
    port, workspace_id=workspace_id, title="私人日常事项", grant_id=artifact_grant_id)

# 2) 显式绑定（**必须**显式给 artifact_id；本模块没有 latest_resumable 路径）
items = pdi.PrivateDailyItems(port, artifact_id, workspace_id=workspace_id,
                              grant_id=artifact_grant_id)
binding = items.bind()      # 读回 artifact/subject/head/doc_type；不是我们的文档就抛错

# 3) 默认读（无私有全文）
view = items.metadata_view()          # counts / unread_count / needs_reread_count / items[]
rows = items.items(pdi.ITEM_INBOX)    # 单类型元数据行
snap = items.snapshot()               # metadata + unread + needs_reread

# 4) 显式取文本（只有这两个出口）
items.full_text(memo_id)              # 收藏/位置：拒绝
items.disclosure_excerpt(memo_id, max_chars=40)   # 带 is_excerpt/truncated
items.source_excerpt(acquisition_id)  # 来源失效后拒绝
```

写入与操作语义（D 必须照抄的分支）：

| 返回值 / 异常 | 含义 | D 该做什么 |
|---|---|---|
| `WriteResult.applied=True` | owner 已落盘（可能经 `reconciled=True` 对账得来） | 用 `item_id` / `version_number` |
| `applied=False, detail=NOTHING_TO_WRITE` | 同一意图已存在，**没有发请求** | 当成功处理 |
| `PrivateItemsConflict` | CAS 失败，**没覆盖任何东西** | `items.load(refresh=True)` 后重新规划 |
| `PrivateItemsOwnerRefused(status, reason)` | owner 明确拒绝（含 grant 撤权） | 不重试，按 `reason` 上报；`GOVERNANCE_REFUSED` 是 definite |
| `applied=False, retryable=True` | UNKNOWN 对账后确认**从未落地** | 用 `WriteResult.operation_key` **和同一份内容**（含同一个 `now`）重试；不要换内容 |
| `PrivateItemsUnresolved` | UNKNOWN 无法对账 | 停手，先把 owner 的未决操作查清 |
| `PrivateItemsSourceUnavailable` | 来源已撤权/失效 | 不能靠摘要/缓存复活 |
| `PrivateItemsVersionMismatch` | head 与文档自称的版本不一致（别人写过这个 artifact） | 重新绑定/人工确认，别硬写 |
| `PrivateItemsContractRefused` | 本地合同拒绝，**一个字节都没发出去** | 修调用 |

修正/失效语义：`revise_memo` 改正文（`revisions` 计数 +1）；`mark_source_unavailable` 让该
source+revision 的正文一律拒绝；来源出现更新版本后旧位置自动 `needs_reread=True` 且保持自己的
`source_revision`。

D 需要新 owner 命令时的落点：`private_daily_items.py` 只用
`LifeArtifactPort` 的 `READ_OPS`/`COMMAND_OPS` 白名单；如果 D 需要 archive/tombstone 之类的
新命令，应扩 `life_artifact_port.py` 的窄白名单（本单**没有**改它），不要绕过 port 直接开 socket。

## 5. 未实现项 / 阻塞

- **没有**手机/房间界面、CLI、cycle 接线——那是 D 的范围；本单只交协议与适配器。
- **没有** World owner 集成：房间 object refs 只吃调用方给的真实观察映射，本单不启动 World、
  也不声称位置权威。位置/placement 仍是 World 的事，本模块只存"观察链接"。
- **没有**实现或测试删除私人条目（施工单明确要求本轮不做）。
- **没有**真正去取阅读材料：`record_source_acquisition` 只**记录**调用方给出的取得回执与范围/
  页码证据。真正取页面的能力属于阅读来源自己的 owner，本版本不存在，也没有冒充。
- **没有**为私人事项建第二真相库或 phone 库；文档形态（紧凑 JSON + `doc_type`）是 v1 原型，
  若将来有真正的 Phone owner，这里是迁移点。
- `metadata_view` 是**按类型白名单**：将来加私有字段必须显式加进白名单（或加 `has_*` 标志），
  这是故意的。
- 阻塞：无。夹具根 `/tmp/nyaw05r.jdK3q` 不存在时会自动创建；只需该目录可写。

## 6. 本单修掉的两个真缺陷（自测发现）

1. `operation_key_for` 要求每个部分非空，而可选 id 传了 `""` → 所有不带显式 id 的写入都会
   在**发出前**抛 `LifeArtifactError`。改为在 `_commit` 里丢掉空部分（空部分只来自"未给的可选
   id"，不会把两个不同意图混成一个）。
2. item id 原先由**含 head** 的 operation key 派生，导致同一张收据在 head 变化后再收一次会新建
   一条收件。改为 item id 只由**意图**派生（`intent_digest`），于是重投的收据/重复的同内容意图
   会落到自己那条记录上（`NOTHING_TO_WRITE`），不再重复建行。
