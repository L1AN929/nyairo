# 可撤销披露许可 · 用法与边界（2026-10-29）

## 这是什么

一件作品想进入本地收件箱（`local_inbox`）之前，Supply owner 会额外检查一张**披露许可**。
许可只绑定四件事：

- 主体 `subject`
- 来源作品 `artifact_id`（canonical id）
- 来源的**精确版本** `version_id`
- 现有本地目标 `local:owner`（渠道仍是固定的 `local_inbox`）

许可里没有正文、没有 token、没有分享草稿 id；它不是一块新的权限库，而是复用既有
`GovernanceAuthority` 的 grant 生命周期（签发 / 查询 / 撤销 / revision / CAS / 回执）。

**写许可和披露许可是两个域**：`COMMIT_MANAGED_ARTIFACT` 不能当披露许可用，披露许可也不授予写权。
一次真实投递必须同时满足两者。

## 发放 / 查询 / 撤销（本机 operator）

```bash
# 绑定一个准备好的实例：socket 和 subject 都从实例自己的 instance 文件读，不读环境/密钥
python scripts/disclosure_authority_cli.py --instance <instance-root> issue \
    --source <artifact:...> --version <artifactversion:...> --operation-id <id> [--max-uses 1]
python scripts/disclosure_authority_cli.py --instance <instance-root> query
python scripts/disclosure_authority_cli.py --instance <instance-root> revoke \
    --grant-id <grant:...> --operation-id <id>
```

`--socket <life-supply.sock>` 仍是低层入口（隔离根/测试用）；显式 `--subject` 或 `--socket`
与 `--instance` 不一致会被拒（`SUBJECT_INSTANCE_MISMATCH` / `SOCKET_INSTANCE_MISMATCH`），
不能借参数把 operator 动作改投到别的主体/别的主人。调用者身份来自 socket 对端的 uid
（`SO_PEERCRED`），不是请求 JSON；CLI 不能自称 operator。输出只有 id / status / reason / count，
没有正文。

## 发送时的行为

1. `send_local` 仍走既有 Sharing 状态机（草稿 CAS、来源版本、受众/渠道、已投递检查）。
2. 到 owner 真正提交那一版 `LOCAL_DELIVERED` 时，owner **不以客户端自报的 header 为准**：
   - 新信封的 `source_artifact_id` 必须真的是该主体的作品，`source_version_id` 必须真的
     属于该来源作品（`DISCLOSURE_SOURCE_VERSION_MISMATCH`）；
   - 新信封的 source / version / subject / audience / channel 必须与 **canonical head 上
     已保存的那份草稿**完全一致（`DISCLOSURE_DRAFT_BINDING_MISMATCH`）；第一版不能直接是
     投递信封，也不能从 DROP/EXPIRED/已投递的头上继续发（`DISCLOSURE_DRAFT_BINDING_MISSING`）；
   - 首行声称是分享信封却解析不了（重复 header、缺 `text:`、未知 state）会被具名拒绝
     （`DISCLOSURE_ENVELOPE_MALFORMED`），不会被当成普通文本放行。
3. 有且只有一张当前有效的许可时，owner 在**同一个 `BEGIN IMMEDIATE` 协调事务**里
   reserve → begin → consume 披露许可和写许可，再写版本。许可读取/预留与写版本用同一个 UOW
   连接（`authority._connection()` 加入 `LifeSupplyDB.active_unit_of_work`）；撤销是另一条
   `BEGIN IMMEDIATE`，因此先撤后发会被具名拒绝，先发后撤不会抹掉已记录的本地回执。
4. 没有许可：`DISCLOSURE_GRANT_REQUIRED`；已撤销：`DISCLOSURE_REVOKED`；已过期：
   `DISCLOSURE_EXPIRED`；主体/版本/目标/能力不符各有具名原因；未知 grant：
   `DISCLOSURE_GRANT_UNKNOWN`。全部是**确定性拒绝**，不是 `UNKNOWN`。
5. **等价入口同样封住**：`create_artifact` 不能用投递信封做第一版；`import_projection`
   拒绝从外部投影文件导入投递信封；旧 `service/server.py` 入口没有 operator 查询端口，
   对投递信封（含坏信封）显式失败关闭（`DISCLOSURE_ADMISSION_UNAVAILABLE`），普通文本行为不变。
6. 旧草稿的读取、延后、丢弃不受影响；只有发送被拒。普通作品读取不受任何披露许可影响。
7. 源作品换版后，旧许可不会自动覆盖新版本，也不会自动绑定 latest；需要显式新 prepare
   与新的精确许可。已发生的 `LOCAL_DELIVERED` 与 receipt 不因后来撤权而消失。
8. 旧 v1 分享信封格式完全不变（本许可在信封之外），因此旧草稿仍可读；旧草稿发送同样需要许可。

## 明确不做 / 未验证

- 没有外发、没有多受众、没有关系加分；`local receipt` 不是「用户已收到/已读」。
- 未验证：跨 uid 管理员、真实人认证、并发撤销与提交的极端交错（超出一个事务内的串行点）、
  其它受众、完整隐私/F150/EL4、G1 或 24h soak。`SO_PEERCRED` 的信任边界与既有本机模型一致。
- 不迁移旧实例，不改 Memory 准入/删除/恢复权威，不新增第二权限 owner。
- 测试/夹具新建的隔离根一律保留（`mkdtemp`，无 rmtree/自动 cleanup/裸 unlink）。
