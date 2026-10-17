# `private_daily_items` 用法（一页）

> v1 原型协议（B 包）。整份 typed 私人事项文档 = **一个** ManagedArtifact 的**一个** artifact，
> 每次修改提交一个新版本。持久真相在 Supply，本模块只做编解码/命令/投影；房间位置仍是 World 的
> 事实，本模块只存"观察链接"。没有 phone 数据库，也没有第二真相库。
> 完整回执与真实 owner 证据：`docs/engineering/life-v1-b-result.md`。

## 1. 绑定（显式，绝不用 latest_resumable）

```python
import life_artifact_port as lap
import private_daily_items as pdi

port = lap.LifeArtifactPort("/run/life-supply.sock", "chiyo:home")   # 现有 port

artifact_id, _ = pdi.create_document_artifact(                          # 走 owner 的 create_artifact
    port, workspace_id=workspace_id, title="私人日常事项", grant_id=artifact_grant)

items = pdi.PrivateDailyItems(port, artifact_id,                        # 必须显式给 artifact_id
                              workspace_id=workspace_id, grant_id=artifact_grant)
items.bind()   # {'artifact_id','subject_id','workspace_id','title','lifecycle',
               #  'head_version_id','version_number','doc_type','doc_artifact_binding','item_count'}
```

绑到作品或别的 JSON artifact 上会在**读**的时候就抛 `PrivateItemsContractRefused`
（`DOCUMENT_NOT_JSON` / `NOT_A_PRIVATE_DAILY_ITEMS_DOCUMENT`），不会当作文档用。

## 2. 写

```python
items.add_memo(title="桌上的半页草稿", body="先写两行。")        # -> WriteResult
items.revise_memo(memo_id, body="改第二行。")
items.record_source_acquisition(source_id="book:plants", source_revision="rev-1",
                                receipt_ref="receipt:pages-1-4", obtained={"pages": [1, 2, 3, 4]},
                                excerpt="实际拿到的那一页文字")     # excerpt ≤480 字，超长拒绝
items.set_reading_position(source_id="book:plants", source_revision="rev-1", position=4)
items.mark_unclear(source_id="book:plants", source_revision="rev-1", note="第三页没看懂。", position=3)
items.save_reference(source_id="song:evening", source_revision="rev-1", label="傍晚那首歌",
                     receipt_ref="receipt:save-1")                # read=False，不取全文
items.receive_item(receipt_ref="receipt:msg-1", from_ref="user", kind="message",
                   summary="到了但没看")                          # co_presence=False
items.mark_read(inbox_item_id, evidence_ref="receipt:opened-1")
items.mark_source_unavailable(source_id="book:plants", source_revision="rev-1",
                              reason="grant revoked")
items.note_item_at_observed_object(memo_id, observation=world_observation, object_id="obj:desk")
```

不变量：`obtained`/`receipt_ref`/`evidence_ref`/`reason` 都不能空；阅读位置必须已有该
source+revision 的**可用**取得记录；`understanding` 恒为 `UNKNOWN`，本模块没有把它改成"懂了"的
API；来源失效后它的正文/摘要一律 `PrivateItemsSourceUnavailable`。

## 3. 读

```python
items.metadata_view()            # counts / unread_count / needs_reread_count / items[]，无私有全文
items.items(pdi.ITEM_INBOX)      # 单类型元数据行（白名单字段 + has_text/has_summary/has_note/...）
items.snapshot()                 # metadata + unread + needs_reread，private_text_included=False
items.needs_reread_positions()   # 旧 source revision 的书签（不被静默改指到新版本）

items.full_text(memo_id)                          # 备忘正文 / 未懂 note / 收件 summary / 来源 excerpt
items.disclosure_excerpt(memo_id, max_chars=40)   # {'excerpt','chars','truncated','is_excerpt'}
items.source_excerpt(acquisition_id)
```

`full_text` 对**收藏**和**阅读位置**直接拒绝（它们本来就不持有文本）。`metadata_view` 里
**没有** body / summary / note / locator / excerpt，只有 `has_*` 标志。

## 4. 写入结果的四种分支

| 结果 | 含义 | 处理 |
|---|---|---|
| `applied=True` | owner 已落盘（`reconciled=True` 表示是 UNKNOWN 后对账出来的） | 用 `item_id` / `version_number` |
| `applied=False, detail=NOTHING_TO_WRITE` | 同一意图已在文档里，**没发请求** | 当成功 |
| `retryable=True` | UNKNOWN 对账后确认**从未落地** | 用 `WriteResult.operation_key` + **同一份内容**（含同一个 `now`）重试 |
| 异常 | `PrivateItemsConflict`（CAS 失败，没覆盖）/ `PrivateItemsOwnerRefused`（含撤权）/
  `PrivateItemsUnresolved`（对账不出结果，停手）/ `PrivateItemsVersionMismatch` /
  `PrivateItemsContractRefused`（本地拒绝，零字节外发）/ `PrivateItemsSourceUnavailable` |

适配器**不会**在 UNKNOWN 后自行重发；也不会在 CAS 冲突时覆盖别人的版本。

## 5. 自测

```bash
PYTHONDONTWRITEBYTECODE=1 <venv>/bin/python -m unittest tests.test_private_daily_items -v
```

真实 Life Supply owner（真 socket / 真 canonical SQLite）跑 14 项；夹具根是唯一
`mkdtemp`（默认 `/tmp/nyaw05r.jdK3q/b-private-items-*`，可用 `NYAIRO_LIFE_V1_B_ROOT` 覆盖），
**保留不清理**。
