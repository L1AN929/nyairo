# Memory 绑定入口窄核对（2026-10-22，只读）

范围：承接 current.json rev93 与 NOW 第六节，按 v2.0 EP-09 路线核对**当前既有显式启用入口**能否用于持久实例
`/root/nyairo-day-20261022`。只读代码与文档，未访问实例、未运行测试/服务/模型，除本文件外未写任何东西。
不是重做 Memory，不修跨备份防复活，不施工，v3.0 未读未引用。

## 结论

1. 现成 CLI 足以完成"给该实例新建绑定"这一件事，不需要改代码：
   `python scripts/phone.py enable-instance-memory --instance <phone root> --owner chiyo --operation-key <key>`。
   `--instance` 是 **phone root**，不是 nested 的 Life root。
2. 入口自带备份、只写三个环境名、独立 `controls.db`、只读健康检查、幂等与"半套文件"拒绝。
   但它**没有**同实例停止 / lease FREE 前置门，也不检查 `serve` / resident 是否在跑；这是唯一具名缺口，
   只能由操作者用只读预检 + 停服纪律补上，不是阻塞级缺陷。
3. 推荐方案 A：复用现成 CLI，配只读预检与停服；不改代码。方案 B（把租约/停止门写进入口）只在用户
   明确要求"强制拦截"时立项；手工写配置或直接 sqlite 建库（方案 C）否决。
4. **owner 名字不能随意改指**：已有绑定不匹配时会拒绝（`MEMORY_BINDING_OWNER_MISMATCH`）。
   当前入口没有迁移操作；不能由此推导“只能删库重建”。本工单禁止删库或重指向。默认 `chiyo` 有代码出处，
   但现场若有显式 Memory owner 或 resident 身份配置，必须先按既有优先级核对，不以默认值覆盖。
5. `operation_key` 只是必填入参，不落盘、不是操作账本；幂等来自"已记录的显式绑定"，不是 key 去重。

## 入口事实

### CLI 与路径推导

- 命令定义：`scripts/phone.py:251-273`；参数 `--instance` / `--owner`（可省）/ `--operation-key`（必填，`:261`）。
  CLI 不暴露任何注入点（`components/phone/life.py:1040-1041`）。
- root 语义：`phone_root()` 只做 `expanduser`（`life.py:170-171`）；nested = `<root>/one-day`
  （`:183-186`），读 `<root>/one-day/instance.json`（`:189-203`）；install root 取环境
  `CHIYO_LIFE_INSTALL_ROOT`，缺省 `<root>/one-day/life`（`:206-212`）；Memory 目录 =
  `<install>/memory`（`:964-967`）。
- 对目标实例的推导：`/root/nyairo-day-20261022` 是 phone root——`day_capability_probe.prepare_instance`
  是在该 root 上用 `demo.prepare_demo` 建实例，再 `read_nested_life(root)`（`scripts/day_capability_probe.py:170-206`）。
  因此按代码：nested = `/root/nyairo-day-20261022/one-day`，install =
  `/root/nyairo-day-20261022/one-day/life`，memory =
  `/root/nyairo-day-20261022/one-day/life/memory`。**现场实际记录值未核实**（未连宿主、无 SSH），
  install 路径以 instance.json 里的 `CHIYO_LIFE_INSTALL_ROOT` 为准。
- 身份来源：owner 的写入名就是 resident 自己读的第一个名字，`MEMORY_SUBJECT_ENV =
  ("NYA02_MEMORY_SUBJECT", "NYA02_SUBJECT")`（`autonomous_life_resident.py:75-77`）；没有该名字时
  resident 用字面量 `"chiyo"`（`:633`、`:663`），`life.py:107` 的 `MEMORY_DEFAULT_OWNER` 也是 `"chiyo"`。
  db / controls 同理取 `NYA02_MEMORY_DB` / `NYA02_MEMORY_CONTROLS_DB`（`resident:75-76`）。一次新建绑定
  实际写这三个名字：`life.py:1169-1172`。
- 该实例现状：文档一致记为 `UNBOUND`（NOW 与 `2026-10-27-v04-memory-product-service.md`），本次未连
  实例复核；按现状会走"新建绑定"分支，而不是复用分支。

### 保护面（有）

- **operation key 必填**：`MEMORY_OPERATION_KEY_REQUIRED`（`life.py:1078-1080`）。
- **nested 必须存在且可读**：`NESTED_LIFE_INSTANCE_MISSING` / `NESTED_LIFE_INSTANCE_INVALID`
  （`:1070-1077`）。
- **幂等 / 不覆盖已有绑定**：已有显式 db 名字时 `reused=true`，只做一次只读健康检查，不新建不重建
  （`:1093-1126`）；owner 不一致 → `MEMORY_BINDING_OWNER_MISMATCH`（`:1110-1113`）。
- **不接管半套文件**：`memory/` 下有文件却没记录绑定 → `MEMORY_FILES_EXIST_UNBOUND`（`:1135-1138`）。
- **备份**：写前把 `instance.json` 做字节副本 `<name>-backup-<UTC µs>`，冲突
  `CONFIG_BACKUP_COLLISION`，失败 `CONFIG_BACKUP_FAILED`（`:976-985`、`:1153-1157`）。
- **独立 controls**：`memory/controls.db` 与 `evidence.db` 分开建，账本以该实例 owner 初始化
  （`:1134`、`:1159-1163`）。
- **只写三个名字**：其余 environment 逐键原样保留，原子替换并保留原 mode（`:988-998`、`:1169-1176`）；
  离线用例 `test_enable_writes_only_the_three_memory_names_and_keeps_run_and_budget` 钉住。
- **只读健康检查**：`sqlite3 mode=ro`，READY 才算 ok；非 READY 写明 reason，文件保留、不重建
  （`:802-950`、`:1182-1211`）。
- **不启服务、不动 run/预算**：`processes_started=0`、`service_started=False`，`restart_required=True`
  （`:1037-1038`、`:1188`）；实现只碰 instance.json 与新建 memory 目录。

### 保护面（无 / 缺口）

- **没有 lease 门**：`check_single_lease()` 存在（`life.py:531` 起，纯读 flock）但 `enable_instance_memory`
  全段（`:1019-1212`）没有调用；`EXIT_LEASE_HELD=4`（`:62`）在此路径未被使用。
- **不检查 serve / resident 是否在跑**，也不看 serve marker。而 `serve` 会写 `instance.json`
  （`live_day.py:36` 自述 "It never writes instance.json itself（serve_instance still does ...）"）；
  enable 用函数开头读到的 payload 原子覆盖（`life.py:1173-1176`），并发写会丢更新。
- **不校验 owner 合理性**：只与"已有绑定"比对，不判断 persona 是否该用别的名字。
- **operation_key 不落盘**：仅在报告里回显（`:1056`），不是持久操作账本。
- **半途失败不回滚**：`MEMORY_CREATE_FAILED` / `CONFIG_WRITE_FAILED` 会把已建文件列进 `written` 并留在盘上
  （`:1164-1179`）；下次重试会被 `MEMORY_FILES_EXIST_UNBOUND` 挡住，需要人工处置，不能当"重跑一次就好"。

## 安全前置

在该实例上执行前，操作者侧按顺序（本工单一条都不执行）：

1. **停掉该实例的 serve 与 resident**。理由有两条：并发写 instance.json 会丢更新；运行中的 resident 也不会
   热加载新绑定（`restart_required=true`）。
2. **只读确认 lease FREE**：`python -X utf8 scripts/phone.py life-preflight --instance /root/nyairo-day-20261022`
   —— 该命令纯读、零写、零模型、不连接（`life.py:1219-1229`），lease 检查在 `:1289`。`HELD` 就停手。
3. **只读确认 `memory/` 不存在或为空**。若已有文件却无绑定，enable 会拒绝（`MEMORY_FILES_EXIST_UNBOUND`）；
   这时不删文件、不手工收编，停下来问。
4. **确认备份可写**：`instance.json` 同目录需可写，备份失败即拒绝（`CONFIG_BACKUP_FAILED`）。
5. **记录执行前基线**（只读）：instance.json 里 `CHIYO_AUTONOMOUS_RUN_ID`、`REQUEST_UTC_DAY_MAX_ENV`、
   `REQUEST_RUN_WINDOW_MAX_ENV`、`CHIYO_LIFE_INSTALL_ROOT`、`NYA02_RESIDENT_STATE`、`NYA02_SUBJECT`，
   以及账本 run / utc_day 计数、Activity 与原材料引用。执行后逐项对照。
6. **owner 先核实再启用**：已有显式 Memory owner 不覆盖；未绑定时按 resident 的既有身份优先级核对，全部缺省才用 `chiyo`。Memory owner 与手机 workspace subject 不强制相等；身份来源不一致或需要新命名语义时停止，不自行重指向。
7. **不允许手工补写配置或 SQL**：绑定只从 nested `instance.json` 读出，shell 里 export `NYA02_MEMORY_*`
   不生效（`2026-10-24-v04-phone-life-memory-preflight.md`）；绕过 enable 直接改 JSON 或建 sqlite，
   会得到无备份、无健康检查、无幂等保护的状态，否决。

执行后应满足（代码保证 + 离线用例钉住）：
- environment 只新增 `NYA02_MEMORY_DB` / `NYA02_MEMORY_CONTROLS_DB` / `NYA02_MEMORY_SUBJECT` 三项，其余逐键不变；
  `run:one-day`、96/96 预算、Activity、旧 START、原材料引用都在 enable 的写面之外。
- 新增文件只有 `<install>/memory/evidence.db`、`<install>/memory/controls.db` 和一份
  `instance.json-backup-*`。
- 返回 `READY` + exit 0 才可继续；非 READY 是 `ok=false` / exit 3，文件留着、不重建，需按 reason 处置。
- `restart_required=true`，要下一次启动才生效——重启与后续实跑不在本工单范围。

## 方案

**A（推荐，不改代码）**：直接用现成 CLI，配上面 1–7 的只读预检与停服。它能覆盖"新建绑定"的全部路径，
缺口仅是"没有强制停止门"，用只读 `life-preflight` 加人工纪律补。零成本、无新契约、无新 owner。

**B（仅在用户要求强制拦截时立项）**：在 `enable_instance_memory` 备份之前加
`check_single_lease(...) == FREE` 与 serve marker / live pid 检查，`HELD` / 在跑 → 具名拒绝
（可复用 `EXIT_LEASE_HELD=4`）。这是行为变更 + 新拒绝面，需要任务书与正负控测试，不在本工单执行。

**C（否决）**：手工改 instance.json 或直接 sqlite 建库。绕过备份、健康、幂等与"绑定只从 nested
instance.json 读"的既有约束，不采纳。

需要停问的：换 owner 语义（persona）、删已有 memory 文件、改绑定重指向、新增恢复权威——都超出本入口。

## 验收命令

离线（此套用例在 POSIX 才有意义）：

```bash
python3 -m unittest tests.test_v04_memory_product.EnableInstanceMemoryTests -v
```

覆盖 10 个用例：创建 + 备份 + 不启服务、只写三键且保 run/预算、第二次幂等零字节变化、不覆盖已有绑定、
坏绑定只报不重建、缺 nested、缺 operation key、半套文件拒绝、非 POSIX 具名拒绝、启用后健康只读。
`owner_bound_only` 在 Windows 上 skip（`tests/test_v04_memory_product.py:78-80`），本机 Windows 实跑只会
3 项通过 / 7 项 skip；**skip 不算通过**，要有 POSIX 结果才算数。

现场只读（操作者在宿主执行，本工单未执行）：

```bash
python -X utf8 scripts/phone.py life-preflight --instance /root/nyairo-day-20261022
python -X utf8 scripts/day_capability_probe.py status --root /root/nyairo-day-20261022
```

两条都不启动服务、不建库、不写盘（`live_day.py:1250-1257`；`day_capability_probe.py:437-446`）。
`status` 在 `WORLD_BODY_SOCKET` 已绑定时会对**已在运行**的 World owner 做一次有界观测，那是 socket
连接，不是启动。**HTTP 服务只由 `phone.py serve` 启动**，本工单禁止；`enable-instance-memory` 本身也不起服务。

交付文档验收：

```bash
python -X utf8 -c "from pathlib import Path; p=Path('docs/engineering/v04-memory-binding-entry-20261022-audit.md'); s=p.read_text(encoding='utf-8'); assert len(s)>800; assert all(x in s for x in ['## 结论','## 入口事实','## 安全前置','## 方案','## 验收命令','## 未验证']); print('ENTRY_AUDIT_OK')"
git diff --check -- docs/engineering/v04-memory-binding-entry-20261022-audit.md
```

## 未验证

- 未访问 `/root/nyairo-day-20261022`：实际 `CHIYO_LIFE_INSTALL_ROOT`、`memory/` 是否已存在、
  当前 owner 相关环境值，均未核实。
- 未运行任何测试（只给 ID 与预期），未启动实例/服务，未调用模型，未执行 enable。
- 未验证绑定后 resident 是否真正召回记忆、是否影响选择、是否同实例续接；READY 只证明接线成立。
- 未量化 `serve` 与 enable 并发写 instance.json 的实际概率，只据 `live_day.py:36` 的注释与
  `_write_instance_file` 的读—覆盖结构推断存在丢更新面。
- 跨备份恢复防复活仍是 FAIL，EP-09 整体未过；绑定既不修复也不遮蔽它。
