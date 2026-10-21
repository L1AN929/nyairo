# 0.4 P：主体 Life 的 Memory 参与预检 —— 交付结果（UNBOUND / CONFIGURED / READY / BLOCKED）

> 表述纪律：本层只证明**"记忆有没有绑、绑了以后是不是真的健康可召回"这件事被诚实报告**。
> 它**没有**调用任何模型、**没有**进入真实实例、**没有**新建记忆库或表，也**没有**把记忆变成生活的前置硬门。
> "她现在会用过去做决定了"这句本轮不成立。

- 仓库：`D:\nyairo-rc1-body-backend`（HEAD `41e818f`，全部既有 dirty 保留）
- 独占写集（本轮只动了这些）：
  - 改：`components/phone/life.py`
  - 新增：`tests/test_phone_life_memory_preflight.py`、本文、
    `.agents/notes/implemented/feature/2026-10-24-v04-phone-life-memory-preflight.md`
- 未触碰：`components/life/scripts/**`（含 `autonomous_life_cycle.py` / `autonomous_life_assembly.py` /
  `one_day_life.py` / `autonomous_life_resident.py`）、`components/native/**`、PDI / Supply / World /
  手机 `service|server|demo` / `web/**`、任何 `instance.json`、任何配置、任何 profile。

---

## 1. 真实接线：runtime 到底读哪几个名字

新检查**没有发明环境变量**。它取的就是 `autonomous_life_resident` 自己解析的那三组名字
（`components/life/scripts/autonomous_life_resident.py:69-71`）：

| 用途 | runtime 读的名字（按优先级） | 使用点 |
|---|---|---|
| M0 数据库 | `NYA02_MEMORY_DB` → `CHIYO_LIFE_MEMORY_DB` | `_memory_ports():290`、`_continuity_source():318`、`start():427` |
| 控制账本 | `NYA02_MEMORY_CONTROLS_DB` → `CHIYO_LIFE_MEMORY_CONTROLS_DB` | `_memory_ports():301`、`_continuity_source():330` |
| 记忆主人 | `NYA02_MEMORY_SUBJECT` → `NYA02_SUBJECT`，都空则字面量 `chiyo` | `:299`、`:329` |

`components/phone/life.py:669 memory_env_names()` 运行时从 resident 模块**读取**这几组名字，模块不可
import 时才退回同一组字面量（`:101 MEMORY_ENV_FALLBACK`）。因此：

- 名字集合与被检查的 runtime 同源，不会出现"预检说有、run 时找不到"的漂移；
- 生产写入这些名字的**唯一**途径是 `one_day_life.prepare(..., memory_db=...)`
  （`one_day_life.py:438-441`，只有显式给了 `memory_db` 才写 `MEMORY_*`），也就是说"有没有绑记忆"
  在实例清单里本来就是**显式**的；
- 手机 `features` 里若有同名 env 会被现有 `build_binding()` 透传（不是本轮新增行为），
  `OWNED_ENV_NAMES`（`one_day_life.py:119-129`）继续保证操作者 shell 的 export 不可能绑上记忆。

本轮**没有**给 `PHONE_LIVE_FIELDS` 增加 `memory_db` 等手机字段：那会改动手机实例清单语义与绑定顺序，
属于施工单禁止的写集（实例清单/配置）。所以当前事实是：**记忆绑定仍然只能来自 nested Life 清单的
`environment`，与 run 时一致**。这个缺口写在这里，不在本轮私自补。

## 2. 状态语义（五档，互不顶替）

`components/phone/life.py:774 check_memory()` 返回：

```python
{
  "status": "UNBOUND" | "CONFIGURED" | "READY" | "BLOCKED" | "UNKNOWN",
  "reason": str | None,          # 具名原因码，见下表
  "detail": str | None,
  "optional": True,              # 记忆是可选件，不是生活前置
  "degraded": bool,              # True = 这一轮记忆没有（或没能证明）参与
  "blocking": bool,              # True 仅当 BLOCKED
  "path_configured": bool,       # "有配置路径"——最弱的一档事实
  "recall_ready": bool,          # True 仅当 READY：真的能召回
  "database"/"database_env", "controls_database"/"controls_env"/"controls_defaulted",
  "owner"/"owner_env"/"owner_defaulted", "phone_subject",
  "subject_matches_phone": None,   # 恒为 None = NOT_COMPARABLE：两个身份不同域，从不做等式
  "conversation", "path_exists", "is_file", "controls_exists", "controls_is_file",
  "readable", "integrity", "m0_table_present", "life_outcomes", "governance_healthy",
  "controls", "probe": "read_only_sqlite_mode_ro", "writes": False, "created": False,
  "env_names": {...}, "error": str | None, "note": "..."
}
```

| status | 判据 | 进 `blocked`？ | `degraded` | `recall_ready` | reason |
|---|---|---|---|---|---|
| `UNBOUND` | 三组名字里**没有任何** M0 数据库 | 否 | 是 | 否 | `MEMORY_OPTIONAL_UNBOUND` |
| `CONFIGURED` | 路径记录在案、文件在，但**健康/召回没被证实**（探针没作答） | 否 | 是 | 否 | `MEMORY_HEALTH_UNVERIFIED` |
| `READY` | 文件在 + `mode=ro` 可开 + `PRAGMA quick_check` = ok + 有 `evidence_events` + 账本只读投影 `healthy`（主人取自 runtime 环境，**不**与手机 subject 比相等） | 否 | 否 | 是 | `None` |
| `BLOCKED` | 明确记录了却不可用（见下 5 个原因码） | 是 | 否 | 否 | 具名 |
| `UNKNOWN` | 绑定本身无法解析 | — | 是 | 否 | — |

`CONFIGURED` 与 `UNKNOWN` 都不进 `blocked`（记忆不是所有生活的前置硬门），但 `life-run` 会在**构造
resident 之前**以 `MEMORY_HEALTH_UNVERIFIED` 拒绝启动（见 §4）——"有配置路径"既不是"健康可召回"，
也不是"没绑"。

`BLOCKED` 的五个具名原因码：

- `MEMORY_DB_MISSING` / `MEMORY_DB_NOT_A_FILE` —— 路径不在 / 不是普通文件（目录冒充也算）；
- `MEMORY_DB_UNREADABLE` —— `mode=ro` 打不开或读不了（含"这是个文本文件不是 SQLite"）；
- `MEMORY_DB_INTEGRITY_FAILURE` —— `quick_check` 不是 `ok`；
- `MEMORY_DB_SCHEMA_MISMATCH` —— 没有 `evidence_events` 表（这不是 M0）；
- `MEMORY_CONTROLS_DB_MISSING` / `MEMORY_CONTROLS_DB_NOT_A_FILE` / `MEMORY_CONTROLS_UNHEALTHY`
  —— 账本不在、不是文件、或它自己的只读投影判定不健康；

**身份分域，永不强制相等**：Memory 的 `owner`（`NYA02_MEMORY_SUBJECT` → `NYA02_SUBJECT` → `chiyo`）
与手机实例的 `subject`（Supply workspace 身份）是两个域。`one_day_life.prepare(memory_subject=...)`
（`one_day_life.py:279-281`，写入点 `:441`）**就是**为了允许两者不同（例如 Memory persona
`chiyo.persona` 配 workspace subject `chiyo`）。因此 `subject_matches_phone` 恒为 `None` =
NOT_COMPARABLE：两个身份只并排报告，不匹配**不是** `BLOCKED`，`life_outcomes` / `governance_healthy` /
`conversation` 照常给出（`conversation` 用的是**记忆主人**的 id，不是手机 subject 的）。这也不会把别的
实例的绑定拉进来：owner 取的永远是**本实例**环境里记录的那个。

**"配置路径存在" ≠ "健康可用"** 是这条检查的核心区分，实测成立的最典型例子：只写
`NYA02_MEMORY_DB` 而不写 `NYA02_MEMORY_CONTROLS_DB` 时，runtime 会把 M0 库**自己**当控制账本
（`LifeExperiencePort(controls_db=None) → self.evidence_db`），而 M0 里没有 `memory_controls` 表，
于是账本投影 `healthy=False`、`recent()` 永远返回空。检查这里报 `BLOCKED` +
`MEMORY_CONTROLS_UNHEALTHY`，并带上 `controls_defaulted: true` 与一句说明——**不能**报 READY。
（而 `one_day_life.prepare` 在没给 `memory_controls_db` 时正是这么写的，见 `one_day_life.py:440`。）

## 3. `life-preflight` 新增输出（全部只增不改）

- `checks["memory"]`：上面那个完整字典（`life.py:1024`）。
- `report["memory"]`：同一份的平铺摘要 + `participation`
  （`NOT_BOUND_OPTIONAL_DEGRADATION` / `BOUND_HEALTH_UNVERIFIED` / `BOUND_READY_READONLY_VERIFIED` /
  `BOUND_BUT_UNUSABLE` / `UNRESOLVED`，常量见 `:116 MEMORY_PARTICIPATION`）。
- `report["bindings"]`：多出三组记忆环境名的 `bound`/`source` 行（来源仍是 `nested_life` /
  `phone_features` / `absent`，不新造来源）。
- `report["assembly"]`：多出 `memory_status` / `memory_bound` / `memory_recall_ready` /
  `memory_degraded` / `memory_optional: True`。**`run_ready` 与 `feasible` 都没有把记忆算进前置**：
  未绑定不会让 `feasible`/`run_ready` 变假，只有 `BLOCKED` 才通过既有 `blocked` 机制让它变假。
- `verified_claims`：`read_only` 增 "memory databases (sqlite mode=ro, quick_check, sqlite_master)"；
  `not_performed` 增 "memory table creation or migration" 与 "memory owner construction"。

## 4. `life-run` 行为：降级要说明，错误不许静默

顺序没变（清单 → nested → cross-binding → uid → supply → model → **memory** → switch → 预算 → lease
→ supply health），记忆闸放在 model 之后（`check_model` 在 `:1174`，记忆闸 `:1182` 起）：

- `UNBOUND`：**继续跑**，报告 `memory.status=UNBOUND`、`participation=NOT_BOUND_OPTIONAL_DEGRADATION`，
  末尾 `note` 追加 `Memory participation=…`。这是"可选降级"，不是"她没有过去"。
- `CONFIGURED` / `UNKNOWN`：**不跑**。在**构造 resident 之前**返回
  `reason=MEMORY_HEALTH_UNVERIFIED`、`exit_code=3`、`model_called=false`、`processes_started=0`，
  `resident_factory` 一次都不构造，`participation` 分别是 `BOUND_HEALTH_UNVERIFIED` / `UNRESOLVED`。
  理由：显式记了记忆却证不出健康，等于"可能读不到过去"，与"没绑"是两件事；按这种状态起一轮，
  就是拿未证实的过去当没有过去。这条闸只挡 `CONFIGURED`/`UNKNOWN`/`BLOCKED`，`UNBOUND` 照旧放行。
- `BLOCKED`：**在构造 resident 之前**返回 `reason=MEMORY_BINDING_BROKEN`，`detail` 带内层原因码
  （例如 `MEMORY_DB_MISSING`），`exit_code=3`，`model_called=false`、`processes_started=0`，
  `resident_factory` 一次都不构造。理由写在代码注释里：带着一个坏掉的过去静默跑，就是"半条 run"。
- `NO_MODEL_PROVIDER` 的优先级没有被改动：无 provider 的实例仍然先报 `NO_MODEL_PROVIDER`
  （test 里用同一 fixture 在"未绑 / 已绑健康"两种情况下各验一次）。

## 5. 只读保证（为什么不会建库/建表/迁移）

- 探针只用 `sqlite3.connect(<resolve().as_uri()> + "?mode=ro", uri=True, timeout=2)`
  （`:696 _readonly_sqlite`）；只跑 `PRAGMA quick_check`、一次 `sqlite_master` 查询、一次
  `count(*)`（`:704 _memory_probe`）。
- **不构造任何 owner**：`EvidenceStore(...)` 会 `mkdir` 父目录、跑迁移并 `chmod 0600`
  （`components/native/src/app/evidence.py:149-153`）；`MemoryControlLedger.initialize()` 会建表。
  两者都没被调用。治理结论来自 `MemoryControlLedger(...).projection()` —— 它本身只开只读连接
  （`memory_control.py:20-23,188-235`），而且这正是 runtime 自己的 continuity 探针用的同一个调用
  （`autonomous_life_resident.py:347`），所以"healthy"在这里和在 run 时是同一个意思。
- 测试侧对 fixture 根做 mode/size/mtime 全量快照，preflight 前后必须完全相等；并且断言
  `memory/` 目录里**没有**新增 `-wal` / `-shm` / `-journal` 文件（`tests/...:513`）。
- 顺带说明：M0 的 journal 模式是 WAL（`components/native/src/app/sqlite_safety.py:8-11`），
  在 `mode=ro` 下打开一个已关闭的 WAL 库不会建 `-shm`；这一点在本机 Python 3.13 / SQLite
  上实测过（先 1 次独立探针，后由上面的测试钉住）。

## 6. 测试与真实 exit

命令（POSIX 用 WSL 的 python3；`PYTHONDONTWRITEBYTECODE=1`，fixture 根落在会话 scratch，**全部保留不删**）。
按收口要求本轮**只跑 6 个 exact ID**，不跑全文件、不跑旧 32：

```bash
cd /mnt/d/nyairo-rc1-body-backend
export PYTHONDONTWRITEBYTECODE=1
export NYAIRO_V04_MEMORY_ROOT=/mnt/c/Users/28556/.pi-desktop/scratch/<session>/v04-memory-fixtures
python3 -m unittest -v \
  tests.test_phone_life_memory_preflight.MemoryHealthTests.test_a_different_memory_persona_is_legal_not_cross_bound \
  tests.test_phone_life_memory_preflight.RunMemoryGateTests.test_run_refuses_when_memory_health_is_unverified \
  tests.test_phone_life_memory_preflight.RunMemoryGateTests.test_run_refuses_an_unresolved_memory_binding_before_a_resident_exists \
  tests.test_phone_life_memory_preflight.UnboundMemoryTests.test_run_reports_memory_unbound_without_blocking_the_limited_run \
  tests.test_phone_life_memory_preflight.RunMemoryGateTests.test_run_records_a_ready_memory_without_claiming_a_model_call \
  tests.test_phone_life_memory_preflight.MemoryHealthTests.test_a_truncated_m0_file_is_an_integrity_failure_not_a_missing_one
```

`Ran 6 tests` → `OK`，**真实 exit 0**（WSL Python 3.12.3 / SQLite 3.45.1；POSIX 上 `EvidenceStore` 可 import，
所以"不同域 owner"那条用的是**真实 M0**）。六个 ID 钉住的就是父级指出的两处缺陷 + 四条必须保持的行为：

| ID | 钉住的事实 |
|---|---|
| `…test_a_different_memory_persona_is_legal_not_cross_bound` | 记忆主人 `chiyo.persona` ≠ 手机 subject `chiyo` **合法**：真实 M0 + 真实账本 → `READY`，`subject_matches_phone=null`，`life_outcomes=1`，不进 `blocked` |
| `…test_run_refuses_when_memory_health_is_unverified` | `CONFIGURED`（探针没作答）→ `MEMORY_HEALTH_UNVERIFIED`，resident 不构造、零写、`exit_code=3` |
| `…test_run_refuses_an_unresolved_memory_binding_before_a_resident_exists` | `UNKNOWN` 与 `CONFIGURED` 同闸：`MEMORY_HEALTH_UNVERIFIED` + `participation=UNRESOLVED`，resident 不构造 |
| `…test_run_reports_memory_unbound_without_blocking_the_limited_run` | `UNBOUND` 不挡 run：`ok=true`、`exit_code=0`、`note` 带 `Memory participation=…` |
| `…test_run_records_a_ready_memory_without_claiming_a_model_call` | `READY` 合法跑通，且不谎报模型调用（`model_called=false`） |
| `…test_a_truncated_m0_file_is_an_integrity_failure_not_a_missing_one` | 真损坏：M0 文件**原地截断 100 字节**，仍能 `mode=ro` 打开、仍有 `evidence_events`，但 `quick_check` 报错 → `MEMORY_DB_INTEGRITY_FAILURE`，不是"缺文件/未绑/不可读"，且 preflight 前后 fixture 快照相等 |

- 文件现在共 **20** 个用例。本轮**没有**重跑全文件，也没有重跑旧 `tests.test_phone_subject_life`。
  早前记录的 `Ran 17 tests / OK (skipped=1)` 与旧 `Ran 32 tests / OK (skipped=5)` 是修正**之前**的快照
  （用例数与身份/闸门行为都已变），本轮不复述为新证据。
- 仍然 skip 的只有 `test_a_genuine_m0_store_database_is_ready` 的 Windows 分支：真实 `EvidenceStore`
  在模块层 `import fcntl`（`app/evidence.py:7`），Windows 上 import 不了；WSL/Linux 上不 skip。
- 早前 CLI 实测里那条 `MEMORY_SUBJECT_MISMATCH` **已作废**（该分支已删除，见 §2），本轮没有重跑 CLI，
  因此不声称任何新的 CLI 结果；身份分域由上面的 pinned test 覆盖。
- 测试 fixture：每个用例一个唯一根 `v04-memory-preflight-<pid>-<us>`；POSIX 上 M0 用真实 `EvidenceStore`，
  Windows 上退回列定义同构的替身（`evidence.py:176-226`）+ 一条真实形态的 `LIFE_VERIFIED_OUTCOME` 行；
  账本一律用真实 `MemoryControlLedger(...).initialize()`；损坏样本只破坏数据文件（原地截断），不删任何东西。
  **没有** unlink / rmtree / TemporaryDirectory / 全局 no-op unlink。

## 7. 局限与已知偏差（都不在本轮私自修）

1. **未在真实实例上跑过**：当前 rc1 手机实例本来就没绑 Memory（`one_day_life.prepare` 没给
   `memory_db`），本轮也不去碰真实实例，因此"整套链在真库上 READY"的证据等级仍是隔离 fixture。
2. **`controls` 默认回落 M0 是生产写法的坑**：按 `one_day_life.py:440`，不给 `memory_controls_db`
   就等于把 M0 当账本 → 本检查会 `BLOCKED`。这是真实行为被如实暴露，不是误报；要跑通得显式给一个
   真账本库。是否调整 `prepare` 的默认属于别的写集（`one_day_life.py` 是 C/父级），本轮只登记。
3. **手机清单不能自带记忆绑定**：`PHONE_LIVE_FIELDS` 未增字段（见 §1），所以记忆只能来自 nested
   `environment`。想"手机实例显式声明记忆"需要单独授权（会改绑定顺序与清单语义）。
4. **Windows 上 M0 fixture 是"同构替身"**：只保证列与约束形状一致；真实 store 的迁移/WAL
   由 POSIX-only 用例覆盖。产品检查本身不依赖 `fcntl`，所以 Windows 上 READY 仍然可达。
5. `UNKNOWN` 仍是保留档：所有真实失败都能落到更具体的 `BLOCKED`/`CONFIGURED`，本轮没有任何**产品**
   路径产出 `UNKNOWN`（宁可具名，不给模糊档）。但 `life-run` 的闸门把它和 `CONFIGURED` 放在同一支
   （`:1202` 起），并且有一条 pinned 用例用注入的 `check_memory` 钉住"将来真出现 `UNKNOWN` 也照样在
   构造 resident 之前拒绝"。
6. `integrity 损坏` 现在有真实样本：`test_a_truncated_m0_file_is_an_integrity_failure_not_a_missing_one`
   把 M0 文件**原地截断 100 字节**（不删文件），`quick_check` 返回
   `"*** in database main ***\nFragmentation of 4 bytes reported as 0 on page 3"` → 走
   `MEMORY_DB_INTEGRITY_FAILURE`。注意更严重的破坏（例如把数据页清零）会让 `quick_check` **抛**
   `database disk image is malformed`，那在代码里落到 `MEMORY_DB_UNREADABLE` 而不是 integrity 档——
   这是有意的：先能打开才谈得上完整性问题。
7. `life_outcomes` 只是一次 `count(*)`（`memory_owner` + `source_origin`），**不**代表"能召回的内容
   已经被治理投影筛过一遍"；真正的筛法留给 `LifeExperiencePort.recent()`，本轮不复制它。
8. 本轮没有任何模型调用、没有搜密钥、没有改 provider、没有常驻、没有 commit/push/deploy。
   0.4 未发布。
9. 本轮收口**只跑了 6 个 exact ID**（按父级要求，不跑全 20、不跑旧 32），因此除这 6 条之外的用例
   只有修正前的历史记录，没有本次重跑证据。


## 8. 不宣称

- 不宣称"记忆已经进入决策候选"（那是 `MEMORY-CAUSE-1` 的事，本轮只做"参不参与"的诚实预检）。
- 不宣称"主体会使用过去"：`READY` 只表示"库可读、治理健康、账本投影对**本实例记录的记忆主人**健康"。
  `READY` **不**表示"记忆主人等于手机 subject"——两者是不同域，本层从不做这个等式。
- 不宣称"未绑定等于没有过去"：恰恰相反，`UNBOUND` 是显式命名的可选降级。
