# v0.4 A：Memory 产品化（可显式启用的实例记忆 + 可检索生活经历）结果

阶段单：`NYAIRO-v0.4-跨日记忆与个人实践-大阶段单.md`（A 写集）。
设计依据：`15-产品记忆与自主机制完整设计.md` §4.5 / §5.1–§5.4、`18-全局整合裁定与建设优先级.md`。
仓库：`D:\nyairo-rc1-body-backend`，HEAD `41e818faaa24115fbf43db2977b15585c04144af`，全部 dirty 保留。

本文件只写**实际交付**与实际跑过的验证；没有实现的东西一律写在"未做/限制"里。

## 1. 交付物

| 文件 | 性质 | 内容 |
| --- | --- | --- |
| `components/life/scripts/life_memory_service.py` | 新增 | `LifeMemoryService`、`memory_service_from_env(env)`、`memory_env_names()` |
| `components/native/memory_runtime_v1/life_experience_port.py` | 改 | 新增 `readiness()` / `search()` / `read()` 三个纯读接口 + `MemoryReadUnavailable`（原有 `append_outcome` / `recent` / `prompt_view` 语义未变） |
| `components/life/scripts/life_experience_memory_consumer.py` | 改 | owner 改取 `port.subject`；`owner_confirmed_context` 合同落地 |
| `components/phone/life.py` | 改 | 新增 `enable_instance_memory(root, owner, operation_key)`、`memory_root()`、私有备份/原子写助手 |
| `scripts/phone.py` | 改 | 新增唯一 CLI 入口 `enable-instance-memory` |
| `tests/test_v04_memory_product.py` | 新增 | 35 个 A 面用例（本阶段唯一新测试文件） |

未改：B 的 `components/phone/service.py` / `server.py` / `web/*`；C 的 cycle / assembly / resident / AG1 / AG2 / AR0 / continuation-source；`components/native/src/app/evidence.py`（M0 唯一写口与 schema 未动）；`native/memory_control.py`（既有 DELETE/CORRECT 治理机制原样复用）。

## 2. 稳定接缝（B/C 调用）

```python
from life_memory_service import LifeMemoryService, memory_service_from_env

service = memory_service_from_env(env)      # 未绑定也返回 service（status=UNBOUND）
service.status()                            # -> {"ok", "status", "data"}
service.list_experiences(query="", limit=20, since=None)
service.read_experience(experience_id)
service.context(query="", limit=5)          # 有界正文（≤600 字符/条）
```

envelope 形状：`{"ok": bool, "status": str, "reason": str|None, "detail": str|None, "data": {...}}`。
`status` ∈ `READY` / `UNBOUND` / `BLOCKED` / `UNAVAILABLE` / `NOT_FOUND`；`ok` 只在 `READY` 为真。

* `data.binding_status` 与 `status` 同值；`data.participation` 复用手机预检词汇
  （`BOUND_READY_READONLY_VERIFIED` / `NOT_BOUND_OPTIONAL_DEGRADATION` / `BOUND_BUT_UNUSABLE` / `UNRESOLVED` / `NOT_RECALLABLE`）。
* 行字段固定为 `experiences` 里每条的 `experience_id / event_id / occurred_at / text / source_ref /
  activity_ref / action_ref / artifact_ref / version_ref / owner_result / outcome_kind`（`data.count` 同步给出）。
* **故障具名不空库**：任何非 `READY` 的读取都把 `data.experiences`（或 `data.experience`）设为 `None`，
  而不是 `[]`；原因码见下表。
* 原因码：`MEMORY_OPTIONAL_UNBOUND`、`MEMORY_PORT_UNAVAILABLE`、`MEMORY_BINDING_UNRESOLVED`、
  `MEMORY_DB_MISSING`、`MEMORY_DB_NOT_A_FILE`、`MEMORY_DB_UNREADABLE`、`MEMORY_DB_INTEGRITY_FAILURE`、
  `MEMORY_DB_SCHEMA_MISMATCH`、`MEMORY_CONTROLS_DB_MISSING`、`MEMORY_CONTROLS_DB_NOT_A_FILE`、
  `MEMORY_CONTROLS_UNHEALTHY`、`MEMORY_GOVERNANCE_PROBE_FAILED`、`MEMORY_EXPERIENCE_ID_REQUIRED`、
  `MEMORY_EXPERIENCE_NOT_RECALLABLE`、`MEMORY_READ_FAILED`。
* `read_experience` 的 `NOT_FOUND` 故意**不区分**"没有这个 id / 是别人的 owner / 被治理删除或纠正过"，
  避免用一个查询去探测治理决定，也保证任何情况下都不返回旧正文。

`memory_service_from_env(env)` 只读 resident 自己解析的三组名字
（`NYA02_MEMORY_DB|CHIYO_LIFE_MEMORY_DB`、`NYA02_MEMORY_CONTROLS_DB|CHIYO_LIFE_MEMORY_CONTROLS_DB`、
`NYA02_MEMORY_SUBJECT|NYA02_SUBJECT`），不扫描、不猜、不创建任何文件；构造 port 只在内存里做，没有 DB 访问。

## 3. 只读与检索的落地方式

* 读取全部走 `LifeExperiencePort.readiness/search/read`：只用 `sqlite3` 的 `mode=ro` URI 打开**已存在**文件，
  先 `PRAGMA quick_check` + 一次 `sqlite_master`，再用 `MemoryControlLedger.projection()`（内部本来就全 `mode=ro`）
  做治理过滤。**`EvidenceStore(...)` 一次都没构造**，所以 GET 不会 mkdir / 迁移 / chmod / 建 `-wal`。
* `search(query=...)`：把 query 按空白切分（最多 8 个词、全部小写、`%`/`_`/`\` 转义），每个词都必须在
  `content` 或该行自己的 `source_refs_json` 里出现（SQL `LIKE ... ESCAPE`），再按 `occurred_at DESC` 取 `limit`。
  因此"最近 N 条都不相关、更早的一条才相关"能被真正查到，而不是只截最近 5 条。空 query 等价于"最新 N 条"。
* `since` 用 `occurred_at > since`（严格大于，半开区间）。
* 治理之后才返回：`projection.recalls()` 会剔掉被 DELETE 的 turn 与被 CORRECT 的旧事件；投影不健康时
  `readiness()` 直接 `BLOCKED: MEMORY_CONTROLS_UNHEALTHY`，`search/read` 抛 `MemoryReadUnavailable` 而不是返回空表。

## 4. 显式启用：`phone.life.enable_instance_memory`

```python
from components.phone.life import enable_instance_memory
report = enable_instance_memory(root, "chiyo", "enable-memory-1")
```

CLI（唯一新增入口）：

```
python scripts/phone.py enable-instance-memory --instance <root> --owner chiyo --operation-key enable-memory-1
```

行为（报告字段：`ok/status/created/reused/restart_required/binding/backup/written/health/processes_started/service_started`）：

1. 只在该函数被**显式调用**时写盘。GET / `life-preflight` / `check_memory` 永不初始化任何库。
2. 目标位置是**该实例自己的** `<root>/one-day/life/memory/`（或该实例 `CHIYO_LIFE_INSTALL_ROOT` 下的 `memory/`），
   文件名沿用 `run_resident.sh` 的 `evidence.db` + `controls.db`；不写任何全局路径，不碰任何别的实例。
3. 先备份配置：`one-day/instance.json-backup-<UTC 时间戳>`（`shutil.copy2` 原样字节，已存在则拒绝，
   不覆盖任何旧备份），再原子替换 `instance.json`（同目录临时文件 + `os.replace`，保留原文件 mode）。
4. 只写三个环境名：`NYA02_MEMORY_DB`、`NYA02_MEMORY_CONTROLS_DB`、`NYA02_MEMORY_SUBJECT`。
   `CHIYO_AUTONOMOUS_*`（run_id、请求上限、run 窗口）与其它字段一律原样保留，测试逐个断言。
5. 幂等：已记录显式绑定时**不新建、不覆盖、不重建**，只做一次只读健康检查后报告
   （`reused=true`、`created=false`、`restart_required=false`、`status` 照实给 READY/BLOCKED）。
   第二次调用对整棵树做 mode/size/mtime 快照，必须完全相等。
6. 不重建不健康库：已绑定但控制账本不健康 → 具名 `BLOCKED` + `health.reason`，文件与配置一个字节都不动。
7. 半套文件不接管：`memory/` 下已有 `evidence.db`/`controls.db` 却没有绑定记录 → `MEMORY_FILES_EXIST_UNBOUND`。
8. 缺 `one-day/instance.json` → `NESTED_LIFE_INSTANCE_MISSING`；空 `operation_key` → `MEMORY_OPERATION_KEY_REQUIRED`；
   已绑定 owner 与请求的 owner 不同 → `MEMORY_BINDING_OWNER_MISMATCH`（绝不改指）。
9. **不启动任何服务**（`processes_started=0`、`service_started=False`），只是把绑定写好；
   `restart_required=true` 表示正在跑的 resident 要由操作者自己重启才会用上新绑定。
10. 非 POSIX 平台（M0 store 因 `fcntl` 不可 import）→ `UNAVAILABLE` + `MEMORY_STORE_UNAVAILABLE`，
    且在**备份之前**拒绝，磁盘零变化。

## 5. consumer：owner 与"确证结果"合同

* **owner 只取 `port.subject`**。旧的 `subject_id.split(".")[0]` 已删除：handoff 里写 `chiyo.global`、
  `chiyo.host` 或任何别的实例 id 都不能决定这条记忆归谁；port 没有 subject 时 `DEFER` +
  `MEMORY_OWNER_UNRESOLVED`（不猜、不写）。
* **`owner_confirmed_context` 合同**（C 可选提供，C 负责 owner 验证）：

  ```python
  {"artifact_id": "...", "version_ref": "...", "title": "...",
   "result_description": "...", "outcome_confirmed": True}
  ```

  只有这五个 key 会被读取。`outcome_confirmed` 必须是**字面 `True`**；`artifact_id` 必须出现在该 handoff 的
  `artifact_refs` 里；`version_ref` 必须是该事件真实携带的某个引用（`artifact_refs/fact_refs/settlement_refs/`
  `progress_refs/action_refs/world_refs/decision_refs/adoption_refs/causal_parent_refs/completion_evidence_ref/`
  `activity_ref` 之一）；`result_description` 必须是非空字符串并被截断到 320 字符；`title` 可选。
  通过后正文变成"标题；结果描述；作品 X；版本 V；所有者回执 R；发生在 T。"，并额外写入
  `artifactversion` 引用（`version_ref` 列就是它）。任何一条不满足就**整段丢弃**，回落到原来的通用句子；
  receipt 里只给内容无关的码：`ABSENT / NOT_A_MAPPING / NOT_CONFIRMED / DESCRIPTION_MISSING /
  ARTIFACT_MISSING / VERSION_MISSING / TITLE_NOT_TEXT / ARTIFACT_NOT_REFERENCED / VERSION_NOT_REFERENCED /
  UNSETTLED / USED / USED_WITH_IGNORED_KEYS`。白名单之外的 key（例如模型 `self_note`）从不进入正文。
  `UNKNOWN_EFFECT` 等未定结果不会被这段描述升级成成功。
* 仍然只走 M0 唯一 append（`port.append_outcome`），去重 key 不变（handoff 的 `lexp:` id）；
  失败仍 `DEFER` 并留在 AG-2 的 `pending_handoff_ids`，不回滚作品或结算。
* C 侧不提供该 mapping 时行为与之前完全一致（`owner_confirmed_context="ABSENT"`）。

## 6. 测试与退出码

```
# POSIX（WSL，Python 3.12.3）：真实 M0 EvidenceStore + 真实控制账本
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 \
  python3 tests/test_v04_memory_product.py; echo EXIT=$?'
```

**实跑结果（只跑本文件 + 两个精准影响文件，不跑全库）**

| 命令 | 平台 | 结果 | 退出码 |
| --- | --- | --- | --- |
| `python3 tests/test_v04_memory_product.py` | WSL / Python 3.12.3 | `Ran 35 tests ... OK (skipped=1)` | **0** |
| `python tests\test_v04_memory_product.py` | Windows / Python 3.13.15 | `Ran 35 tests ... OK (skipped=31)` | **0** |
| `python3 tests/test_world_daily_life_world_memory_text.py`（consumer 文本精准影响） | WSL | `Ran 4 tests ... OK` | **0** |
| `python3 tests/test_phone_life_memory_preflight.py`（`phone/life.py` 精准影响） | WSL | `Ran 20 tests ... OK` | **0** |
| `python3 scripts/phone.py enable-instance-memory --instance <隔离根> --owner chiyo --operation-key cli-1` | WSL | `ok=true created=true status=READY restart_required=true service_started=false written=3 backup=是` | **0** |
| `python scripts\phone.py enable-instance-memory --instance <无 one-day 的根> --operation-key cli-1` | Windows | `BLOCKED / NESTED_LIFE_INSTANCE_MISSING`，磁盘零变化 | **3** |

skip 的 1 条（POSIX）是"只在 Windows 才有意义"的用例；Windows 上 31 条 skip 是真实读写/consumer 用例
（M0 store 在该平台不可 import）加上同一门禁，剩下的 4 条实际执行（未绑定具名、零写、缺实例拒绝、M0 不可用具名）。

覆盖：显式 enable / 幂等 / backup / GET 零写（整树 mode+size+mtime 快照）/ owner 分域 /
较旧相关检索 / `since` / 治理 DELETE 阻断正文 / 缺库·账本缺表·账本损坏具名 / consumer 用 `port.subject`
（跨主体）/ `owner_confirmed_context` 六种拒绝 + 采用 + self_note 忽略 + 未定结果不升级。

只跑上面这些，不跑全库、不新长时 qualification；`PYTHONDONTWRITEBYTECODE=1`，用 `ast.parse` 代 `py_compile`
（6 个改动文件全部 `AST OK`）。fixture 根留在 `/tmp`（WSL）与会话 scratch 下，不删不移。

## 7. 已知限制（未做）

1. **原实例没有改动、也没有启用**：`enable_instance_memory` 只对隔离 fixture 跑过；真实 rc1 实例本轮
   **没有**调用（阶段单要求），生产内存路径仍未绑定。
2. **Memory 读路径是 POSIX-only**：M0 store（`components/native/src/app/evidence.py`）在模块层 `import fcntl`，
   Windows 上整个 port 不可 import，因此 Windows 上只保留"未绑定/具名拒绝/零写"这几条用例，真实读写用例 skip。
3. **`context()` 不是语义检索**：只有"全词包含 + 时间倒序"，没有向量、没有同义、没有权重；词数上限 8，
   没有打分排序。这是刻意的确定性实现，够"较旧相关经历也能被查到"，但不是语义搜索。
4. **一条经历一个 M0 事件**：没有 episode 聚合、没有 milestone / life period / narrative / self belief
   的晋升链（设计 §5.1–5.2 的后半段不在本轮 A 写集内）。
5. **没有新的删除 API**：`MemoryControlLedger` 的 DELETE/CORRECT 原样复用；本轮 A 不提供物理删除，
   也不越过用户的物理删除禁令。
6. **`enable_instance_memory` 的幂等靠"已记录绑定"判定**，`operation_key` 只做校验与回显，没有单独的
   操作账本（没有新增存储）。重复调用的第二个 key 与第一个不同也不会被拒 —— 因为第二次走的是"复用已绑定"分支。
7. **手机清单仍不能自带记忆绑定**（`PHONE_LIVE_FIELDS` 未改，见
   `v04-memory-preflight-result.md` §1/§7）；`enable_instance_memory` 写的是 Life 实例的 `environment`。
8. **未跑**：全库回归、全树 SHA、任何真实模型调用、真实实例 `life-run`。
