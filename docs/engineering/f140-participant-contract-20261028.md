# F-140 参与者身份契约（2026-10-28）

一句话：**来话属于谁，由这个实例自己的参与者账本回答；没有登记的关联就诚实写 UNRESOLVED。**

## 1. 选型

`components/native/src/app/participant.py` 只是 Telegram 单通道的 JSON 配置解析器：id 由配置手写，
没有别名、端点、创建来源，也不关联来话。它不能满足实例域注册表，因此本切片新增窄 owner：

- 文件：`components/life/scripts/participant_registry.py`
- 存储：`<Life state root>/participant_registry.sqlite3`（与 `life_local_contact.json` 同一实例域）
- 事务：SQLite `BEGIN IMMEDIATE` + busy timeout；同 endpoint 唯一约束；operation key 唯一
- 不碰 Supply / World / Memory 的 canonical 表、合同、controls 或准入；旧 root 不迁移、不导入

## 2. 事实

| 事实 | 谁写 | 说明 |
| --- | --- | --- |
| `participant_id` | owner | `participant:<24 hex>`，不可由显示名/账号/正文推导 |
| 显示名 / 别名 | owner | 可同名，不作唯一键，不自动合并；≤64 字 / 每人 ≤16 个别名 |
| `created_at` / `created_by` / `source` | owner | 时间取 owner 时钟；`created_by`/`source` 固定 `operator:cli`，调用方不能自报 `system`/`trusted` |
| `endpoint` | operator CLI | `(provider, account_key)` 唯一；同一人可显式绑多个通道；已指向别人的 endpoint 拒绝改绑 |
| `event_ref -> participant` | operator CLI | 先经**本实例真实 inbox** 验证事件存在；不按名字/`source_ref` 推断 |
| operation key | owner | 同 key 同 payload 重放；同 key 异 payload 具名拒绝 |

参与者是**身份，不是权限**：不继承 grant / capability / Diary / World / Supply 权限，也没有关系分、
信任度或发送能力。显示名是元数据，不是模型指令。

## 3. operator CLI

```bash
python scripts/participant_registry_cli.py --instance <instance root> \
  create --display-name "小林" --alias "Kobayashi" --operation-key operator:create-1

... list [--limit N]
... get --participant-id participant:...
... bind-endpoint --participant participant:... --provider local_inbox --account-key desk-1 \
      --operation-key operator:endpoint-1
... link-event --event-ref contact_evt:... --participant participant:... \
      --operation-key operator:link-1
... resolve-event --event-ref contact_evt:...
... summary
... binding
```

`--instance` 走仓库已有的实例加载器（手机根用 `phone_service.load_instance`，Life 根用
`one_day_life.read_instance`），自己导出：

| 事实 | 来源 |
| --- | --- |
| Life state root | 嵌套 Life 实例记录的 `NYA02_RESIDENT_STATE`，否则 `<实例>/one-day/life/state`（one-day 根为 `<根>/life/state`） |
| subject | 手机实例的 `subject`（inbox 与手机读面用的同一个值）；one-day 实例取 `subject_id` / `NYA02_SUBJECT` |
| home | 与手机来访面同一顺序：实例环境 `CHIYO_WORLD_HOME`/`HERMES_HOME` → `contact_home_ref` → `world_home` |

`canonical_life_state.json` 的 `subject_id` 是 Life/global 域（标准日即 `chiyo.global`），与实例/
inbox subject 不是同一个概念；`binding` 如实同时报告，不比较相等。

`--state-root` / `--subject` 仅保留兼容：只有该根已经带有可核对的身份（同 subject 的
`life_local_contact.json`，或已有参与者账本由 owner 校验其 meta subject）时才放行；空目录 +
调用方自报 subject 不能开第一本账（`PARTICIPANT_INSTANCE_UNVERIFIABLE`）。实例入口下调用方再传
与实例不符的 `--state-root` / `--subject` / `--home-ref` 一律具名拒绝（`2`）。

退出码：`0` 成功/幂等重放/合法答案（含 `UNRESOLVED`），`2` 具名拒绝、冲突或调用方参数与实例矛盾，
`3` 账本或实例读不出来/无法核验（**不是空表**）。没有 HTTP 写接口、没有公开注册。

`link-event` 用 `--instance` 时 home 由实例导出，不需要也不允许另传不同的 `--home-ref`；兼容形式
仍要 `--home-ref`，且必须是创建该实例 inbox 时的同一个 home。它只用于构造真实只读 inbox 核对事件，
不作为身份证据。

### 本机 OS 边界

- 写入口拒绝 symlink 的状态根/账本文件，拒绝非目录、非普通文件，拒绝 group/other 可写的目录与文件。
- POSIX 下要求状态根与账本属于当前 `euid`；账本创建后 `0600`。读取同样不跟随 symlink。
- Windows 没有可核的 ACL：**写入口 fail closed**（`PARTICIPANT_OPERATOR_OS_UNSUPPORTED`），手机只读
  面不受影响；不为此改系统设置。
- `created_by = operator:cli` 是来源标签，不证明调用者是某个真人；同 uid 的其它进程、root 或任意
  同 uid shell 不在本边界内。检查与 SQLite 打开之间仍有 TOCTOU 残差，事务本身用 `BEGIN IMMEDIATE`。

## 4. 手机读面

- `GET /api/phone/participants`（`?limit=N`）→ 有界列表：稳定 id、显示名/别名、来源/时间、端点与
  关联来话计数；无正文。
- `GET /api/phone/participants/<participant_id>` → 单人 + 端点 + 关联来话引用（最多 20 条，计数另给）。
- `GET /api/phone/contact/events/<event_ref>` → 原投影不变，另加 `participant` 关联块：
  `LINKED` / `UNRESOLVED` / `UNREADABLE`。它不复制正文，也不把未关联事件归给本实例主体。
- `chiyo_phone` 只新增 `participants`（列表）与 `participant`（单人）两个**只读**动作。
  没有创建、绑定、发送或修改动作；不受 `phone.private_artifacts` 影响（不含正文）。
- 账本缺失是 `ledger_state=ABSENT` 的空列表；损坏/错实例是具名 `unavailable`，两者不混。

## 5. 本切片不覆盖

外部账号抓取、自动身份推断、关系/信任/权限、消息存储、删除与合并、分支/fork/恢复谱系（F-141）、
跨实例迁移、模型自然采用；也**不**把 `local_inbox` 或 Telegram 配置文件直接当通讯录。
