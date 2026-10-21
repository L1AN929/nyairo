# Agent Note: v0.4 A —— Memory 产品化接缝（可检索生活经历 + 显式启用的实例记忆）

Status: implemented

## Problem

E07 之后，Memory 只有一个"写进去"和"最近 N 条"的窄门，产品这一层还缺三样东西，每一样都会实际说错话：

1. **"最近 5 条"被当成"相关资料"。** `LifeExperiencePort.recent()` 只问"最新的已验证结果"，
   `EvidenceStore.list_events()` 又是升序混源。于是"她以前读过什么/试过什么"这个问题根本没有接口：
   任何调用方（实践工作台 B、主链 C）都只能拿最近 5 条假装相关，或者自己去 grep SQLite。
2. **没有产品层的读接口，且读会被写成写。** 现有唯一能算"能不能召回"的路径是构造 `EvidenceStore`
   （`__init__` 会 `mkdir` + 跑迁移 + `chmod`）与 `ledger.initialize()`（建表）。把它们放在 GET 后面，
   就是"读一次记忆顺便建了个库"，与 `v04-memory-preflight` 定下的"预检零写"直接冲突。
3. **实例没有自己的记忆，能写的人还能写错 owner。** 真实 rc1 实例从未绑定 Memory（`UNBOUND` 是现状），
   而没有任何**显式**入口能在"这一个实例自己的目录下"把证据库+独立控制账本建起来并写进它自己的 Life 环境；
   同时 `MemoryExperienceConsumer` 用 `subject_id.split(".")[0]` 决定 owner，`chiyo.global` / `chiyo.host`
   这类拼写会把这一实例的经历写进另一条流。

另外两件同批必须落地，否则 C 侧"确证结果"永远只是"某工件变了"：

* AG-2 handoff 边界需要一个**有限、可校验**的"所有者确证结果描述/版本引用"通道，且它绝不能变成
  模型自述（`self_note`）洗白成事实的入口。
* 失败必须具名：缺库/账本缺表/账本损坏都必须给出原因码，不能返回一个空数组让人读成"她没有过去"。

## Decision

A 写集内（新增 `components/life/scripts/life_memory_service.py`；改 native `life_experience_port.py`、
`life_experience_memory_consumer.py`、`components/phone/life.py`、`scripts/phone.py`）：

1. **产品接缝** `LifeMemoryService(port=None)`，四个方法 `status() / list_experiences(query='', limit=20,
   since=None) / read_experience(id) / context(query='', limit=5)`，envelope 固定
   `{ok, status, data, reason, detail}`；`status` ∈ `READY/UNBOUND/BLOCKED/UNAVAILABLE/NOT_FOUND`。
   **非 READY 一律把 `experiences`（或 `experience`）置 `None`**，而不是 `[]`：故障具名、不空库。
   `memory_service_from_env(env)` 只读 resident 自己的三组名字，未绑定也返回 service，不扫 DB、不建文件。
2. **纯读检索进 native port**：新增 `readiness()` / `search()` / `read()`，全部 `sqlite3 mode=ro` +
   `PRAGMA quick_check` + `memory_controls` 账本投影；`search` 把 query 按空白切成 ≤8 个词，
   每个词必须出现在 `content` 或该行 `source_refs_json` 里（`LIKE ... ESCAPE`），再按 `occurred_at DESC`
   限量。这样"最近 6 条都不相关、更早两条才相关"能被真正查到。不健康一律抛具名 `MemoryReadUnavailable`。
3. **实例显式启用** `phone.life.enable_instance_memory(root, owner, operation_key)`：只在该函数被显式调用时
   在这个实例自己的 `<install>/memory/` 下建 `evidence.db` + 独立 `controls.db`；先备份 `instance.json`
   （`-backup-<UTC>`），再原子替换，**只写** `NYA02_MEMORY_DB/_CONTROLS_DB/_SUBJECT`；幂等（已绑定就复用，
   整树快照必须不变）、不覆盖已有显式绑定、不重建不健康库、不接管半套文件、不动 `CHIYO_AUTONOMOUS_*`
   （run/预算）、不启动任何服务。CLI 只加一个 `enable-instance-memory`。
4. **consumer 归属与确证合同**：owner 只取 `port.subject`（拿不到就 `DEFER + MEMORY_OWNER_UNRESOLVED`）；
   可选 `owner_confirmed_context = {artifact_id, version_ref, title, result_description, outcome_confirmed}`
   只在"五个 key 白名单 + `outcome_confirmed is True` + `artifact_id ∈ artifact_refs` +
   `version_ref ∈` 该事件真实引用 + 描述非空（截 320 字符）"时使用，并写 `artifactversion` 引用；
   任何一条不满足就整段丢弃、回落通用句子，receipt 只回内容无关的码。未定结果（`UNKNOWN_EFFECT` 等）
   不会被这段描述升级成成功；`self_note` 之类白名单外的 key 从不进入正文。

## Alternatives considered

* **把 `recent()` 直接改造成相关检索** —— 最强理由是"只有一个读法最不容易分叉"。否决原因：`recent()` 是 C 侧
  continuity 的真实依赖（`autonomous_life_resident._continuity_source`），改它的语义等于顺手改了别人的启动路径；
  新增 `search()` 与它并存，`recent()` 一字节不动。
* **在 `LifeMemoryService` 里自己开 SQLite**（不碰 native port）—— 最强理由是"产品层自己控制最省事"。
  否决原因：`component/life/scripts` 直连 Memory 库正是 E07 禁止的形状，而且治理筛法会变成第二份实现。
  port 已有的 `_to_record` + `MemoryControlLedger.projection()` 才是唯一口径。
* **用 `EvidenceStore` 的 `get()`/`list_events()` 实现读** —— 最强理由是"现成代码、字段齐全"。否决原因：
  构造它就会建库/迁移/`chmod`，GET 会写盘；这正是本轮必须钉死的那条线。
* **`enable_instance_memory` 顺手把手机清单 `PHONE_LIVE_FIELDS` 也加上记忆三项** —— 最强理由是"手机实例是
  live 绑定唯一来源，记忆也该在那儿声明"。否决原因：那会改 `build_binding` 的覆盖顺序（一个跨文件契约），
  属于施工单禁改的实例清单语义；本轮只写 Life 实例自己的 `environment`（run 时真正被读的那份）。
* **`operation_key` 落一个操作账本做幂等** —— 最强理由是"严格幂等需要操作身份"。否决原因：那等于为本功能
  再开一个存储，与本轮"不新增存储、复用既有治理"相冲突；实际幂等来源是"已记录的显式绑定"，`operation_key`
  只做必填校验与回显。
* **让 `owner_confirmed_context` 缺失时也用 `world_refs`/`artifact_refs` 拼一句"更可读"的结果描述** ——
  最强理由是"经历文本更有信息量"。否决原因：那正是"把引用当描述"，等于凭空生成事实；只有 C 明确给了
  `outcome_confirmed: true` 的有限描述才配进正文。
* **不健康时返回空列表并让调用方自己判断** —— 最强理由是"接口简单"。否决原因：空列表无法与被治理清空区分，
  会被读成"她真的没有过去"；`None` + 原因码是唯一不撒谎的形状。

## Consequences

* **收益**：B/C 有了一个不会建库、不会串 owner、不会把故障说成空库的读接缝；"较早的相关经历"真的能被检索到；
  治理 DELETE/CORRECT 一旦生效，列表/详情/上下文三处同时不透出正文；实例可以**显式**拥有自己的记忆，
  且启用的每一步（备份、只写三个名字、幂等、不重建坏库、不动 run/预算、不启服务）都有用例钉住；
  consumer 的 owner 不再由 handoff 拼写决定；C 拿到了一个有限、可校验的"确证结果"通道。
* **代价与已知上限**：① 原实例本轮未调用 `enable_instance_memory`（阶段单要求），生产路径仍是 `UNBOUND`；
  ② Memory 读路径 POSIX-only（M0 store 模块层 `import fcntl`），Windows 只能覆盖未绑定/具名拒绝/零写；
  ③ `context()` 是"全词包含 + 时间倒序"的确定性检索，不是语义搜索；④ 仍是一条经历一个 M0 事件，
  没有 episode/milestone/narrative/self belief 的晋升链；⑤ 没有新删除 API，物理删除依然禁止；
  ⑥ `operation_key` 不落账本（见上）。
* 相邻既有决定：[手机 life-preflight 的 Memory 参与预检](./2026-10-24-v04-phone-life-memory-preflight.md)、
  [记忆夹具归属修复](../bug-fix/2026-10-16-memory-fixture-ownership-fix.md)、
  [单一写者隔离](../process/2026-10-16-single-writer-isolation.md)。
  交付结果：`docs/engineering/v04-memory-product-result.md`。

## Verification

* POSIX（WSL / Python 3.12.3，`PYTHONDONTWRITEBYTECODE=1`，fixture 根留在 `/tmp` 不删）：只跑本阶段新增的唯一
  测试文件 `Ran 35 tests ... OK (skipped=1)`，真实 exit 0。skip 的一条是"只在 Windows 才有意义"的用例。
* Windows（Python 3.13.15，`PYTHONDONTWRITEBYTECODE=1`）：同一文件 `Ran 35 tests ... OK (skipped=31)`，真实 exit 0 ——
  真实读写/consumer 用例按门禁 skip，`enable` 在 M0 store 不可 import 时给
  `UNAVAILABLE + MEMORY_STORE_UNAVAILABLE` 且磁盘零变化（该用例实际执行并通过）。
* 精准影响文件（不重复绿组）：`tests/test_world_daily_life_world_memory_text.py`（consumer 文本）`Ran 4 ... OK` exit 0；
  `tests/test_phone_life_memory_preflight.py`（`phone/life.py`）`Ran 20 ... OK` exit 0。
* CLI 实跑：POSIX 隔离根 `ok=true/created=true/READY/restart_required=true/service_started=false` exit 0；
  无 `one-day` 的根 `BLOCKED + NESTED_LIFE_INSTANCE_MISSING` exit 3。
* 零写证据：`list_experiences/read_experience/context` 与 `check_memory` 前后对整个 fixture 根做
  mode/size/mtime 快照必须相等，且不许出现 `-wal/-shm/-journal`。
* 编译：6 个改动文件全部 `ast.parse` 通过（不用 `py_compile`，不落 `.pyc`）。
* 未跑：全库回归、全树 SHA、任何真实模型调用、真实实例 `life-run`。
* 注：施工期间 B 正在写 `components/phone/server.py`，一度因该文件处于半写完状态导致本文件 import 失败；
  等它落成有效版本后重跑即为上面的绿灯。该失败与本轮 A 改动无关，未修改 B 的任何文件。
