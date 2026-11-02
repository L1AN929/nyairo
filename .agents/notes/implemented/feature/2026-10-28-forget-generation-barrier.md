# Agent Note: F-153/F-154 —— 显式忘记接纳与删除代际屏障

Status: implemented（限定范围，见 Consequences）

## Problem

保留权威（`memory_authority` + floor）能挡住旧 evidence/controls 一起恢复，但缺三件事：
可信的 operator 提出入口、删除与“待澄清 hold”的区分、以及完成判据。历史返修确认两处错：
首交在无 retention/uncovered 时仍报 `COMPLETE`（M0 append-only，payload 实际还在）；首交把
`forgetsrc:<digest>` 写进 `MemoryDeletionAuthority.blocked_event_ids`，而该字段按 event id 被
所有既有消费者精确匹配，属于借字段扩域。

## Decision

新增窄协调层 `forget_request.py` + `scripts/forget_request_cli.py`：operator 入口校验 uid/属主/
符号链接/保留路径；case 库只存不透明 refs/状态/计数。屏障先向既有 authority 写**仅 event id**；
payload 仍走 `MemoryControlLedger.apply`；`retry` 同 operation identity 不新增 generation。
`--hold` 是可唯一定位 scope 的保留 case gate：立即扣留、不写 DELETE、跨重开仍扣留、不自动超时；
`confirm` 才显式转成删除。完成判据由 owner 派生：owner ack 后做一次布尔物理残留读，行仍在就只能
`WAITING_RETENTION`，CLI 少传 flag 不能把它填成 `COMPLETE`。源级 retained authority 标
`SOURCE_AUTHORITY_CONTRACT=BLOCKED_NEEDS_OWNER_CONTRACT`。`memory_service_from_env` 把同一个 gate
同时交给 service 与 port；CLI 的 `--case-db` 必须等于实例配置。

## Alternatives considered

* **继续把 source token 放进 `blocked_event_ids`**。最强理由是零 schema 改动、现有消费者按 event id
  求交不会被误伤。否决原因：字段名与全部消费者都是 event-id 语义，只有新 gate 认识 token；
  这是静默扩域，不能声称“未改 owner 合同”，也没解决同一 freshness 问题。
* **给 authority 加 typed source 列/表**。最强理由是语义最干净、读端直接拿 refs。否决原因：
  这是既有 owner 的持久 schema 与合同变更，超出本单批准；改成具名 STOP 留给 owner 决定。
* **hold 也写 DELETE 或自动超时释放**。最强理由是状态简单。否决原因：删除与待澄清不可混，
  自动释放会让待澄清范围悄悄复活；改为 case 保留 + 显式 `confirm`。
* **把派生面（embedding/索引/Reflection/Sharing/Practice）一起清**。最强理由是一次做完 F154。
  否决原因：没有找到可按 source 正式失效的统一 owner 接口；宁停 `WAITING_RETENTION` 不建假清理。

## Consequences

* 收益：hold 立即扣留且不删；旧双库恢复仍扣留；物理行存在时永不误报 `COMPLETE`；owner 失败屏障仍在；
  authority 缺失读/写都具名 fail closed；runtime 与 CLI 读同一 case 库和同一 gate。
* 代价：源级 retained authority 仍受阻（需 owner 合同扩展）；hold 的 case gate 与 authority 同为本机
  保留记录，一起被回滚时本机不能自证最新；每次读仍走 authority projection。
* 未覆盖：派生面统一失效、物理擦除、换伪 ref 识别、跨主机/整机回滚、真人认证、自然语言范围推断；
  只验证本 demo 新建的合成内容，不碰真实数据/旧实例，不据此声称 F154 或 V-C 全过。

## 2026-10-28 授权后补充

上文“源级 authority”与“派生面一起清”当时因未获 owner 合同授权而 STOP；用户随后直接批准扩展
删除权威，三段已在本次落地。实现与取舍见
[F-154 三段落地](../architecture/2026-10-28-f154-typed-source-erasure-recovery.md)，上文历史不改写。

## 2026-10-28 收尾返修

同日首交后被指出：正在使用的 `barrier_seq` 正整数不是 authority（同 owner/seq 可擦 sibling 与
无关 artifact）；typed 三元组存了却按裸 ref 消费；派生由 demo 手造并硬列进 Memory refs；
恢复只是 utility、构造 owner 即可读且正控用了 after 备份；`COMPLETE` 曾在备份仍有正文时被
“受控当前副本”重定义。返修以 owner 发放并复核的 `ErasureGrant`、三元组全等匹配、
`artifact_source_edges` 的逆向闭包、`RecoveryGate` 强制激活门和标题/摘要 tombstone 取代之；
备份仍在时 case 保持 `WAITING_RETENTION`。实现见
[F-154 三段落地](../architecture/2026-10-28-f154-typed-source-erasure-recovery.md)，上文历史不改写。
