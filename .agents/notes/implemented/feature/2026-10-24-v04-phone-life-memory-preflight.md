# Agent Note: 手机 life-preflight/life-run 的 Memory 参与预检（未绑定=可选降级，绑了不可用=fail-closed）

Status: implemented

## Problem

rc1 的手机 `life-preflight`/`life-run` 有九项检查（`nested_life/supply/world/permissions/lease/
switch/model/privacy`），**没有一项说 Memory**。后果不是
"少一行输出"，而是三个具体错误：

1. **没绑记忆被读成"她没有过去"**：`autonomous_life_resident._memory_ports()` 在
   `NYA02_MEMORY_DB` 为空时返回 `(None, None, None)`，`_continuity_source()` 返回 `None`，
   cycle 只能报 `PAST_SOURCE_NOT_WIRED`；而 `one_day_life.prepare` 只有显式给 `memory_db` 才写
   `MEMORY_*`（`one_day_life.py:438-441`）。于是"当前实例压根没绑记忆"这件事在验收报告里
   没有任何一行痕迹，读者只能自己猜。
2. **"配置路径存在"当成"记忆能用"**：`LifeExperiencePort(controls_db=None)` 会把 M0 库
   **自己**当控制账本（`life_experience_port.py:143`），而 M0 没有 `memory_controls` 表 →
   `projection().healthy is False` → `recent()` 永远返回空。`one_day_life.prepare` 在没给
   controls 时正是这么写的（`one_day_life.py:440`）。一个"文件都在"的实例可以完全没有过去可用，
   而预检看不出来。
3. **明确绑了坏库却照跑**：`life_run` 的闸门没有记忆这一档，`check_model`/`check_supply_binding`
   之外没有任何地方会因为"记忆库缺文件/不是 SQLite/主人是别人"而停下，于是"有限 run"会在
   半条记忆上静默跑完，报告里还写着 `ok: true`。

## Decision

在 P 的独占写集内（只改 `components/phone/life.py`）新增一项**纯读**检查，并接进两个 verb：

- `memory_env_names()`（`:669`）**从 `autonomous_life_resident` 模块读取**它真正解析的三组名字
  （`MEMORY_DB_ENV`/`MEMORY_CONTROLS_ENV`/`MEMORY_SUBJECT_ENV`，`resident.py:69-71`），
  取不到才退回同一组字面量（`:101`）。预检因此不可能发明第四种拼法，也不会去猜默认路径
  （`components/life/tests/v02/*.sh` 里那些 `$RESIDENT_DIR/memory/evidence.db` 只是测试脚本的默认）。
- `_readonly_sqlite()`（`:696`）+ `_memory_probe()`（`:704`）：只 `lstat` 两个文件、只以
  `mode=ro` 打开、只跑 `PRAGMA quick_check`、一次 `sqlite_master`、一次 `count(*)`（限定
  `memory_owner` + `LIFE_VERIFIED_OUTCOME`）。治理结论直接用
  `MemoryControlLedger(controls, database, owner, conversation).projection()` —— 与 runtime 自己的
  continuity 探针是**同一个调用**（`resident.py:347`），所以 "healthy" 在预检与 run 时同义。
  **不构造任何 owner**：`EvidenceStore(...)` 会 mkdir/迁移/chmod，`ledger.initialize()` 会建表，
  两者都没被调用。
- `check_memory()`（`:774`）给出五档：`UNBOUND`（未绑定，可选降级）/`CONFIGURED`（路径在但健康
  未证实）/`READY`（可读 + `quick_check` ok + 有 `evidence_events` + 账本只读投影对**本实例记录的记忆
  主人**健康）/`BLOCKED`（`MEMORY_DB_MISSING`/`NOT_A_FILE`/`UNREADABLE`/`INTEGRITY_FAILURE`/
  `SCHEMA_MISMATCH`/`CONTROLS_DB_MISSING`/`CONTROLS_DB_NOT_A_FILE`/`CONTROLS_UNHEALTHY`）/
  `UNKNOWN`（绑定本身无法解析）。
  `optional: True`、`degraded`、`blocking`、`recall_ready`、`path_configured` 分开写，让
  "有配置路径"永远不等于"真的能召回"。
- **身份分域，永不强制相等**：`subject_matches_phone` 恒为 `None` = NOT_COMPARABLE。Memory 的 `owner`
  与手机实例的 `subject`（Supply workspace 身份）是两个域，`one_day_life.prepare(memory_subject=...)`
  （`one_day_life.py:279-281`，写入点 `:441`）**就是**允许两者不同（`chiyo.persona` 配 `chiyo`）；
  不匹配**不是** `BLOCKED`，两个身份只并排报告，`life_outcomes`/`governance_healthy` 照常给出。
  别的实例的绑定也不会被拉进来：owner 只取**本实例**环境里记录的那个。
- `life_preflight` 增加 `checks["memory"]`（`:1024`）、`report["memory"]`（摘要 + `participation`）、
  `bindings` 里三组记忆名字、`assembly.memory_*`；但 `run_ready`/`feasible` 都**不**把记忆算进前置
  —— 未绑定不会让它们变假。只有 `BLOCKED` 通过既有 `blocked` 机制让 `feasible` 变假。
- `life_run` 在 model 闸之后加记忆闸（`:1182` 起）：`UNBOUND` 继续跑并写明
  `Memory participation=…`；`CONFIGURED`/`UNKNOWN` 在**构造 resident 之前**返回
  `reason=MEMORY_HEALTH_UNVERIFIED`（`exit_code=3`、`model_called=false`、`processes_started=0`、
  `resident_factory` 一次都不构造）；`BLOCKED` 同样在构造前返回 `reason=MEMORY_BINDING_BROKEN` +
  内层原因码。`NO_MODEL_PROVIDER` 的优先级没变（无 provider 仍先报它）。
- 身份**不做等式**：记忆主人与手机 `subject` 不同是合法的（见上），因此没有"不匹配就拒绝"这一支；
  该拒绝分支在本轮收口时已删除（父级指出它把两个域强行相等）。
- **没动** `PHONE_LIVE_FIELDS`：给手机清单加 `memory_db` 字段会改实例清单语义与绑定顺序，
  属于施工单禁改的配置/实例写集；本轮只读 nested `environment`，与 run 时一致。这个缺口登记在
  `docs/engineering/v04-memory-preflight-result.md` §1/§7。

## Alternatives considered

- **复用 `life_experience_port` / `EvidenceStore` 直接算"能不能召回"** —— 最强理由是"用 owner 的
  真实路径最有说服力，连治理筛法都一模一样"。否决原因：`EvidenceStore.__init__` 会
  `_secure_dir` + `_initialize()`（建表/迁移）+ `chmod 0600`，`ledger.initialize()` 会建表；
  施工单要求预检零文件创建/零写入。折中是只用它**只读的那一半** —— `ledger.projection()`，
  因为它内部全走 `mode=ro`（`memory_control.py:20-23`）。
- **读 `one_day_life.MEMORY_*_ENV` 字符串常量而不读 resident 的 tuple** —— 最强理由是
  `one_day_life` 是"准备实例"的那一层，看着更权威。否决原因：真正**消费**这些名字的是
  resident，且它有三个 fallback（`CHIYO_LIFE_MEMORY_DB` 等）；只按 `one_day_life` 的单个拼法
  检查，会漏掉一个 runtime 真的会用、预检却报 `UNBOUND` 的绑定。
- **给手机清单加 `memory_db`/`memory_controls_db`/`memory_subject` 三字段（MEMORY-CAUSE-1 提案的 S1）**
  —— 最强理由是"手机实例是 live 绑定唯一来源，记忆也该在那儿显式声明"。否决原因：本轮施工单明确
  只准改 `components/phone/life.py`，不得改实例清单/配置；而且加字段会改变 `build_binding` 的
  覆盖顺序（nested → phone live），等于顺手改了一个跨文件契约。缺口写进结果文档，不改。
- **记忆不健康时仍然跑，只在报告里写一行** —— 最强理由是"记忆是可选项，硬拦会把记忆变成所有生活的
  前置门”。否决原因：施工单第 36 条要求“明确指定但缺损/不匹配按 fail-closed 报告，有限 run 不得默默
  忽略”。所以划了这条线：**没绑**（`UNBOUND`）永不算门；**记了却证不出健康或确实坏**
  （`CONFIGURED`/`UNKNOWN`/`BLOCKED`）才算门，且只拦“构造 resident 之前”这一处，不碰
  pause/lease/预算。收口时父级指出 `CONFIGURED`/`UNKNOWN` 当时仍然放行，等于把“证不出”当成“没绑”，
  已改为 fail-closed。
- **把 `memory` 的 `BLOCKED` 排除在 `blocked` 列表外，只做提示** —— 最强理由是"`assembly.feasible`
  讲的是能装起来，跟记忆无关"。否决原因：`blocked` 是本层唯一的"这个绑定有硬问题"出口，
  一个明确绑了坏库的实例正是硬问题；而"未绑定"根本不进这个列表，可选性没有被吃掉。

## Consequences

- **收益**：`phone.py life-preflight` 现在能明确回答"记忆绑了没、绑了以后能不能召回"，
  `UNBOUND` 与 `READY` 用词上永远不混；`life-run` 不会在坏记忆上静默跑完；三组名字与 runtime 同源；
  预检仍然零写（测试对 fixture 根做 mode/size/mtime 快照，并断言 `memory/` 下没多出
  `-wal`/`-shm`/`-journal`）。
- **代价与已知上限**：① 真实 rc1 实例本来没绑 Memory，本轮没在真库上跑过，证据仍是隔离 fixture；
  ② 按 `one_day_life.py:440`，不给 `memory_controls_db` 的生产写法会被判 `BLOCKED`（真实行为被如实
  暴露，不是误报），是否改 `prepare` 默认值属于别的写集；③ 手机清单仍不能自带记忆绑定（见上）；
  ④ Windows 上真实 `EvidenceStore` 因 `import fcntl` 无法 import，健康档 fixture 用列定义同构的
  替身，真 schema 由 POSIX-only 用例覆盖；⑤ `UNKNOWN` 仍是保留档（产品路径不产出它），但 `life-run`
  已把它和 `CONFIGURED` 放在同一支 fail-closed 闸里，并有 pinned 用例钉住；⑥ `life_outcomes` 只是
  一次 `count(*)`，不复制治理筛法；⑦ 身份分域后不再有“主人不匹配”这条拒绝，`subject_matches_phone`
  恒为 `None`，两个身份只并排报告。
- 相邻既有决定：[手机实例是 Life 唯一 live 绑定来源](./2026-10-23-rc1-phone-life-subject-binding.md)、
  [记忆夹具归属修复（同一 M0 只能一个 owner 写）](../bug-fix/2026-10-16-memory-fixture-ownership-fix.md)、
  [单一写者隔离](../process/2026-10-16-single-writer-isolation.md)、
  [主体私人材料与版本化阅读](./2026-10-24-rc1-subject-materials-version-aware-reading.md)。
  交付结果：`docs/engineering/v04-memory-preflight-result.md`。

## Verification

- 收口轮（父级指出两处缺陷后的窄修）：按父级要求**只跑 6 个 exact ID**，POSIX 用 WSL 的 `python3`
  （Python 3.12.3 / SQLite 3.45.1，`PYTHONDONTWRITEBYTECODE=1`，fixture 根在会话 scratch 且全保留）：
  `Ran 6 tests` → `OK`，**真实 exit 0**。六个 ID：
  ① `MemoryHealthTests.test_a_different_memory_persona_is_legal_not_cross_bound`（真实 M0，`chiyo.persona`
  ≠ `chiyo` 合法 → `READY`）② `RunMemoryGateTests.test_run_refuses_when_memory_health_is_unverified`
  （`CONFIGURED` 不启动）③ `RunMemoryGateTests.test_run_refuses_an_unresolved_memory_binding_before_a_resident_exists`
  （`UNKNOWN` 不启动）④ `UnboundMemoryTests.test_run_reports_memory_unbound_without_blocking_the_limited_run`
  （`UNBOUND` 不挡）⑤ `RunMemoryGateTests.test_run_records_a_ready_memory_without_claiming_a_model_call`
  （`READY` 合法）⑥ `MemoryHealthTests.test_a_truncated_m0_file_is_an_integrity_failure_not_a_missing_one`
  （真损坏 → `MEMORY_DB_INTEGRITY_FAILURE`）。其中 ③ 和 ⑥ 是本轮**新增**的定点用例。
- 文件现在共 20 个用例。本轮**没有**重跑全文件、**没有**重跑旧 `tests.test_phone_subject_life`：
  早前记录的全文件 `Ran 17 tests / OK (skipped=1)` 与旧 `Ran 32 tests / OK (skipped=5)` 是修正**之前**的
  快照（用例数与身份/闸门行为都已变），不复述为新证据。
- 仍然 skip 的只有真实 `EvidenceStore` 那条的 Windows 分支（`fcntl` 仅 POSIX）；WSL/Linux 上不 skip。
- 只读证据：`test_preflight_opens_memory_read_only_and_creates_nothing` 断言 preflight 前后
  fixture 根快照完全相等、`memory/` 目录文件清单不变、没有 `-wal/-shm/-journal`；两条 life-run 拒绝
  用例也各自断言拒绝前后快照相等。
- 未跑：全库回归、`py_compile`、全树 SHA、任何真实模型调用、真实实例 run（施工单禁止/无授权）。
