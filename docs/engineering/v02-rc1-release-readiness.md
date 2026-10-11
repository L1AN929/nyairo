# v0.2 RC1 发布验收与判定（RC1-03）

**分支**：`v0.2-autonomous-life`
**被验收的提交**：`c4671c9f982cf5297376381f07a88bbb0140ebd1`（`v0.2 RC1-02`，本文写作时的 HEAD）
**验收时刻**：2026-10-11，长测快照 `09:56:56Z`
**本批性质**：只做发布验收。**没有**新建功能、**没有**创建 Release、**没有**创建标签、**没有**合 `main`、
**没有**改动任何生产实例、**没有**停止或重置 `/srv/nya02/resident2`。**真实模型调用 0 次**。

---

## 1. 结论摘要

* 工程实现**已完成**到 RC 的范围：T01–T20 中 19 项 PASS，1 项（T12）DEFERRED。
* 证据是**真实**的：真实 owner、真实模型、真实长测记录；本批没有用替身结论冒充真实结论。
* **不给出 GO。** 判定为 **CONDITIONAL_GO**，条件见 §5、§7。理由不是"功能还没写完"，而是：
  ① 仓库自带的引导安装器仍固定下载 **v0.1.0-rc6**（网站那份固定 rc9），仓库里没有任何 v0.2 安装路径；
  ② 仓库根目录缺 `.gitattributes`，Windows 检出会让 `python3 scripts/test.py` 的 `host-boundaries` 变红，
  且 `scripts/bootstrap.sh` 在 bash 下**根本无法执行**；
  ③ 官方测试入口在干净主机上缺测试依赖，`FAILED_SUITES` 不是空的；
  ④ 24 小时长测**没有**跑完，而且正在跑的那份实例是**旧代码**（`e471241`），**即使跑满也不能验证 RC1**；
  ⑤ 旧供应商密钥仍待账户持有人轮换。
* 若 operator 的标准是"没有在本 RC 代码上跑满 24 小时就不发"，那么**今天就是 NO_GO**；本文按"RC 是候选
  版、限制全部如实披露"的口径给 CONDITIONAL_GO。

---

## 2. 已完成 / 未完成

### 2.1 已完成（都有真实证据）

| 能力 | 证据等级 | 关键证据 |
|---|---|---|
| 无用户消息的自主起念 + 真实模型决定 | `E2E_VERIFIED` | E14 S1/S2，`dec:7c7a1922cc318753`，`event_wakes=0` |
| 首次作品编辑真正产生版本与 Action | `E2E_VERIFIED` | Action `actn:6696072f3fbf8df8` / `SUCCEEDED`，v1→v2 |
| 重启后续写同一作品、旧 head 约束新步骤 | `E2E_VERIFIED` | E14 S3，`expected_head` = V2 → V3，operation 不同 |
| World 合法动作真正改变 owner 状态 | `E2E_VERIFIED` | `WORLD_MOVE_E2E: PASS (move)`，revision 1→2 |
| 重启后自主恢复、无重复副作用 | `E2E_VERIFIED` | E14 S3；`submit_call_count=1` |
| 旧 v0.1.0-rc9 profile 兼容升级 | `E2E_VERIFIED` | `T20: PASS (38 checks passed)`（八项断言，已提交字节上复验） |
| 真实写入可被 AR-1 结算（RC1-01） | `E2E_VERIFIED`（本轮只读复核代码，未新增调用） | `managed_artifact_action_adapter.py:369-392`（`resolve_operation`+`read_head` → `artifact_observer` 证据）；`result_settlement_ar1.py:143-165` 双等级 |
| 记录不再说谎（RC1-02） | 隔离复核 | `model.content_writer` 报 `true`；`counters.defer` 从 cycle 常量取值 |
| 真实模型消费真实生活投影（E14 S6） | `E2E_VERIFIED`（**仅**插件上下文 + 一次直接模型调用） | RC1-02 §11.3：`model.answer = 最近在继续写《长测的手记》。` |
| T04/T05/T06/T07/T08–T11/T13/T15/T17/T19 | `TESTED_ISOLATED` | 矩阵逐行 |
| T14 空窗口零调用 | `PASS（机制）` | `B7_CHECK`、`B8_CHECK` + 真实长窗 84 次 `ROUND_BUDGET_EXHAUSTED` |

### 2.2 未完成

1. **T12**：原生生活经历的 CORRECT 入口不存在（产品级缺口，DEFERRED）。
2. **24 小时 / 7 天长测**：未跑完；真实有效观察只有 8.23 小时，且跑的是旧代码。
3. **v0.2 的安装路径**：不存在。两个引导安装器都下载 v0.1 发行包（rc6 / rc9），仓库里没有 v0.2 的
   标签或 Release。
4. **在真实硬件上验证 systemd 单元**：本机 systemd `offline`（`/run/systemd/system` 不存在），没做。
5. **旧供应商密钥轮换**：账户持有人的动作，未做。
6. **自主 Telegram 联系**：不投递（`proactive_enabled=False` 硬编码在
   `autonomous_life_resident.py:269`）。

---

## 3. 验证证据

### 3.1 24 小时长测（只读复核，判定 RUNNING）

快照 `2026-10-11T09:56:56Z`，窗口 `/srv/nya02/resident2/state`（**未停止、未重置**）：

| 项 | 真实值 |
|---|---|
| 真实起点 / 本进程 / 启动次数 | `2026-10-11T01:41:31+00:00` / `01:46:04` / `starts=2` |
| 最后 tick / 已观察时长 / ticks | `09:55:28` / `29637s ≈ 8.23h` / `183` |
| 进程 | pid 2728 存活（`ps -o etime` = `08:10:52`） |
| 真实有效观察范围 | `01:41:31 → 09:55:28`；**唯一空档 53 秒**（重启边界 `01:45:11 → 01:46:04`） |
| 逐 tick 最大间隔 | **197s**（tick 59→60），其余 ≤181s（poll 180s） |
| 检查窗 | 97 个被评估（10 `DISABLED` + 3 `ACTED` + 84 `BLOCKED`），86 次 `NOT_DUE`，1 次事件唤醒 |
| 评估窗最大间隔 | 7315s ≈ 2h02m（`CHECK_INTERVAL_MINUTES=120` 的设计值） |
| 有候选 / 无候选 / 关闭 / 预算拒绝 | 4（3 检查窗 + 1 事件，全 `ACTED`）/ 86 / 10 / 84 |
| 真实模型调用 | **决策 4 + 内容生成 4 = 8 次 provider 请求**（`counters.model_calls = 4`） |
| 真实 Action / owner 确认版本 | 4 / 4（`actn:13d2647d…`、`actn:abd0f18e…`、`actn:9bf96e58…`、`actn:f760bfa4…`；版本 `83a75499…`、`8c424630…`、`462b901c…`、`bf53a9c5…`） |
| 自唤醒 | **没有**。她自己的 4 次写入一次都没变成事件；唯一事件来自外部写入 `op:b86a:external:1` |
| UNKNOWN / pending | AR-0：`pending_unknown_max=0`、`unknown_settlements=0`、`reconciliations=0`、`reconciled_at_start=[]`；AR-1：**4 条 `UNKNOWN_EFFECT`/`OPEN`**（旧代码） |
| 重启是否重复执行 | 没有。journal 重放只重建计数器；4 个动作各 1 个版本；`submit_call_count=1` |
| 影响观察的异常 | 只有一个 53 秒的重启空档，没有进程退出后的长空档、没有"没人跑"的时段 |
| 标签 | `soak_24h = SOAK_RUNNING_24H`、`soak_7d = SOAK_PENDING_7D` |

**关于"墙钟标签是否可信"**：`soak_24h` 只由 `(now - 首次 started_at)` 推出，**不检查进程是否一直在跑**。
本次它给出 `SOAK_RUNNING_24H`，与真实记录**一致**（确实不到 24 小时），所以**这次没有虚报**；但它的机制
本身不可信，本判定不采用它，只采用上面的 journal + 进程证据。**另有一条更严重的口径问题**：该实例的
`CODE_COMMIT = e4712418748e26265d76be2011e28646ce0babf9`（B8-6A），是 HEAD 的祖先但**不含 RC1-01/RC1-02**，
所以它跑满 24 小时后打上的 `SOAK_VERIFIED_24H` **不能**用来验证 RC1 —— 这一点必须写进发布说明。

### 3.2 预算记账（真实数字）

| 名称 | 真实值 | 说明 |
|---|---|---|
| 认知（决策）调用 | **4** | `counters.model_calls`；也是 `max_cognition_calls_per_day=6` 所约束的那个数 |
| 内容生成调用 | **4** | `purpose=artifact_edit`，由 cycle 的 writer 发出（`_LazyStepSpec` 每轮只 materialise 一次） |
| 真实 provider 请求合计 | **8** | 4 + 4 |
| 每日 Action 预算 | `actions_used=4` / `max_actions_per_day=4`（`spent=4`、`max_rounds_per_day=4`） | 4 轮在 `03:46` 前用完 |

**最小显示改动**（本批唯一的代码改动，`components/life/scripts/autonomous_life_resident.py`）：
`summary()` 新增 `model_calls_detail`（`cognition_calls` / `content_generation_calls` / `total_requests` /
`content_generation_scope` / `daily_budget_counts: "cognition calls only"`）。`counters`、`budget` 的键与
语义未动；**没有**新增预算框架、没有改上限、没有改判定。
回归：`B7_CHECK` / `B8_CHECK` / `B8_6A_CHECK` / `B8_CAUSAL_CHECK` 全部 PASS。

### 3.3 三个失败套件（逐条定性）

隔离 venv `/tmp/rc103-venv`（`httpx` / `pytest` / `psutil` / `PyYAML` / `python-dotenv` / `requests` /
`httpx[socks]` / `pytest-asyncio` / `ruamel.yaml`），按 `scripts/test.py` 的**原命令**重跑；未删测试、
未改产品行为、未加无条件跳过。

| 套件 | 定性 | 真实错误文本 | 处理后真实结果 |
|---|---|---|---|
| `cognition-faults` | **(a) 缺测试依赖** | `No module named pytest` | **1 passed**（3 个警告是套件自己注入的故障） |
| `Hermes` | **(a) 缺测试依赖** | `ModuleNotFoundError: No module named 'dotenv'`（`hermes_cli/env_loader.py:13`），先 5 failed + 1 collection error | **29 passed, 0 failed** |
| `host-boundaries` | **(a) 缺依赖 + (b) 检出环境 + (c) 1 个真实缺陷** | 见下 | LF 树：`Ran 71 tests / FAILED (failures=1)` |

`host-boundaries` 逐条：

| 现象 | 数量 | 性质 |
|---|---|---|
| `scripts/bootstrap.sh: line 3: set: pipefail` + `: invalid option name` | 6 | 工作区 CRLF（bash 把 `pipefail\r` 当选项名） |
| `patches/patch_manager.py`：`ValueError: baseline checksum mismatch` | 1 | 工作区 CRLF（基线哈希按 LF 算） |
| `RuntimeError: memory resolver unavailable: sealed_component_mismatch:production_consumption_evaluator_v0_3.py` | 2 | 工作区 CRLF；CRLF→LF 归一后三个文件 sha256 与 `production_resolver.SEALED_SHA256` **逐字符相等** |
| `test_website_and_repository_installers_are_identical`：`b'…v0.1.0-rc6…' != b'…v0.1.0-rc9…'` | 1 | **真实缺陷**：`scripts/bootstrap.sh:6` vs `website/install.sh:6`，只差 2 行 |
| `No module named 'httpx'` / `psutil` / `yaml` | 11 errors | 缺测试依赖 |

根因（(b)）：**仓库根目录没有 `.gitattributes`**，而 `website/.gitattributes` 写了 `* text=auto eol=lf`。
所以同一棵树里 `website/**` 是 LF、根目录下是 CRLF。**这不是产品缺陷，是检出配置缺陷**，但在 Windows 上
足以让 `host-boundaries` 变红，并让 `scripts/bootstrap.sh` 完全不能运行。
方法说明：为把"检出"与"源码"分开，用 `git archive HEAD | tar -x -C /tmp/rc103-pristine` 导出**已提交字节**的
LF 树再跑同一套命令（不修改工作区、不建分支、不建 worktree）。

**没有执行的验收脚本**：`components/life/tests/v02/run_isolated_acceptance.sh` 含
`pkill -f supply_service.py` 与 `rm -rf /srv/nya02`；`run_world_instance.sh` / `run_world_move_e2e.sh` 也含
`pkill`。在长测实例正在跑的时候**不得**直接执行它们，本批因此**没有**重跑 T16（World MOVE）与 isolated
acceptance，只做只读复核；`check_life_v02.py` / `check_life_b7.py` / `check_life_b8.py` /
`check_life_b8_6a.py` 本身不引用 `/srv/nya02`，可以安全单独运行。

### 3.4 文档与代码一致性

本批核对并修正的过期表述（发现时的事实原样保留，另标当前状态）：

| 文件 | 位置 | 原文 | 现在 |
|---|---|---|---|
| `KNOWN_ISSUES.md` | 原 47 行 | `model.content_writer` 恒假、未修 | 标 **已修（RC1-02）**，保留当时现象 |
| `KNOWN_ISSUES.md` | 原 48 行 | `counters.model_calls` 只算决策、未修 | 标 **仍未修**，补真实数字 8 次与显示标签 |
| `KNOWN_ISSUES.md` | 原 38 / 41 / 33 行 | 长测起点 `01:07:06`、三套件"环境性失败"、记录提交 `e471241` | 更新为真实快照、逐条原因、HEAD `c4671c9` |
| `KNOWN_ISSUES.md` | T13 那条 | 端口把 `REVOKED` 归成 `UNKNOWN` | 标 **代码口径已变（B8-8）、撤回路径未复测** |
| `docs/engineering/v02-b8-7-install-and-compat.md` | 原 68 行（§4.2） | `model.content_writer` 恒假、未修 | 标 **已修（RC1-02）** |
| `docs/engineering/v02-b8-7-install-and-compat.md` | 原 71-73 行（§4.3） | 预算少算一次、未修 | 标 **仍未修 + 显示标签**，补真实数字 |
| `docs/engineering/v02-b8-7-install-and-compat.md` | §4.4 / §6 / 新增 §7 | `REVOKED`→UNKNOWN；三套件未重跑 | 逐条状态 + §7 复核表（含 8 failures / 2 errors → 1 failure 的对应关系） |
| `docs/engineering/v02-b8-acceptance-matrix.md` | 原 103 行（第 1 条） | `model.content_writer` 恒假 | 标 **已修（RC1-02）** |
| `docs/engineering/v02-b8-acceptance-matrix.md` | 原 106 行（第 2 条） | `counters.defer` 恒 0 | 标 **已修（RC1-02）** |
| `docs/engineering/v02-b8-acceptance-matrix.md` | 原 110 行（第 4 条） | AR-1 两次 `UNKNOWN_EFFECT` | 标 **已修（RC1-01）**，保留"旧 head 拿不到证据"的边界 |
| `docs/engineering/v02-b8-acceptance-matrix.md` | T14 行 / 24 小时表 / E14 S6 缺口 / 回归段 | 86 次 BLOCKED（把 `NOT_DUE` 数当成 BLOCKED）、`6.33h/ticks=145`、S6 未做 | 更正为 84 `BLOCKED` + 86 `NOT_DUE`；更新为 `8.23h/ticks=183`；S6 已补做且写明证据级别 |
| `docs/engineering/v02-autonomous-life-implementation.md` | §1 AR-1 行、§3、§8、§9、§10、§11.4/§11.5、新增 §12 | T20 未过、长测旧数字、三套件未重跑、三处"未修"待更新 | 全部按当前状态更新，新增 §12 指向本文 |

**仍未对齐、本批未改的**（留给下一批决定）：

* `TESTING.md:99` 仍写着 B8-6 那次观察的起点 `2026-10-11T01:07:06+00:00` 与"本批结束约 24 分钟"。它是对
  **当时那一批**（`/srv/nya02/resident`）的真实历史陈述，不是错，但和现在的长测窗口
  （`/srv/nya02/resident2`，`01:41:31`）不是同一个实例，读者容易混淆。
* `MANIFEST.json` 是 **v0.1.0-rc6** 的发行清单（`schema: chiyo/source-release@1`、
  `version: 0.1.0-rc6`），在 HEAD 上跑 `scripts/verify_manifest.py` 得到 `"valid": false`（27 个
  `changed_or_missing` + 一批 `unexpected_files`，含全部 v0.2 文件）。**它不能用来为 v0.2 做完整性声明**；
  本批**没有**改它（v0.2 的清单属于发行工作）。

---

## 4. 发布条件逐项复核

| 条件 | 真实状态 |
|---|---|
| 新用户的全新安装 | **有真跑过的隔离安装记录**（E13：`/srv/nya02/inst/**`，subject `chiyo-install`，从全新个人目录 → 初始化 → 配置供应商 → 工作区/授权 → 打开自主生活 → 一次合法作品活动 → 回读结果；见 `INSTALL.md`、`docs/USER_GUIDE.zh-CN.md`）。**但没有任何 v0.2 安装器**：`scripts/bootstrap.sh:6` 固定 `v0.1.0-rc6`、`website/install.sh:6` 固定 `v0.1.0-rc9`，两者都下载 v0.1 的 zip；`git tag` 里没有 v0.2。RC1 目前是**源码交付** |
| 升级旧 `v0.1.0-rc9` profile | **PASS**：T20 八项、38 项断言（旧聊天/CLI、旧斜杠命令、旧 M0/M1/M37、旧纠正与删除控制、旧 Supply 作品与授权、自主默认关闭、未授权写入确定拒绝、迁移逐字节完整），在**已提交字节**上复验。**本批没有重跑**（`/srv/nya02/t20` 不在本批可动范围内），引用已记录证据 |
| T01–T20 最终状态 | **19 PASS + 1 DEFERRED**。`E2E_VERIFIED`：T01 T02 T03 T16 T18 T20（6）；`TESTED_ISOLATED`：T04 T05 T06 T07 T08 T09 T10 T11 T13 T15 T17 T19（12）；T14 `PASS（机制）`；T12 `DEFERRED` |
| AR-1 成功 / 失败 / UNKNOWN 对账 | **成功**：owner 确认版本后重读（`resolve_operation`+`read_head`）四条全中 → `artifact_observer` 证据 → `SETTLED / SUCCESS_EFFECT`（RC1-01 在真实 E14 重放上把 `rset:fb8ea78b…` 从 OPEN 推到 SETTLED）。**失败**：`NO_CHANGE` / `CONFLICT` / 治理拒绝各自按自己的规则，不结算成功。**UNKNOWN**：丢回执时保持 UNKNOWN，用**原 operation id** 对账后按真正落地的版本结算（`submit_call_count=1`，不产生第二个版本）；一个**已不再是 head 的旧版本**正确地不产生证据、保持 OPEN（见 §6 限制 2） |
| 自主生活默认关闭 | **是**。`assemble_autonomous_life(..., enabled: bool = False)`（`autonomous_life_assembly.py:256`）且常驻进程不覆盖它；真实记录里开关打开前 10 个检查窗都是 `DISABLED`（"autonomous life is off; nothing was considered"，0 次调用），直到 operator 在 `01:43:09` 写 `autonomous_switch.json` 才进入决策 |
| 权限撤回 | T13 `PASS TESTED_ISOLATED`（在线撤回真的做过：写 v2 → `REVOKED` → 新写被拒 → 作品仍只有 2 个版本）。**口径注意**：B8-7 记录的是"窄端口把 `REVOKED` 归成 `UNKNOWN`"；RC1-03 复核 HEAD 代码发现 B8-8 之后 `reason == GOVERNANCE_REFUSED` 已被映射为确定拒绝（`life_artifact_port.py:440-451`，撤回授权在 `life_supply.py:1184` 返回的正是这个 reason），所以**按现在的代码会分类成 `REFUSED`**。但撤回路径在 B8-8 之后**没有重跑过**，不得写成"已验证" |
| Hermes 现有聊天能力 | **在**。v0.2 没有改聊天链：插件只做只读投影（`pre_llm_call` 钩子注册成功），canonical 状态零写入；4 个 context-engine 测试文件在补齐测试依赖后 **29 passed / 0 failed**；E14 S6 用真实插件投影 + 真实模型跑通一次（证据级别见 §6 限制 6 与 RC1-02 §11.3） |
| 当前长测 | 见 §3.1：真实有效 8.23h，判定 **RUNNING**，7 天不提前判定 |
| 发布文档与代码一致 | 见 §3.4：4 个文档的 12 处过期表述已修；2 处残留（`TESTING.md:99` 的历史起点、`MANIFEST.json` 仍是 rc6 清单）已记录，未改 |

---

## 5. 必须修好的发布阻塞项

1. **引导安装器的版本漂移（真实缺陷，2 行）。** `scripts/bootstrap.sh:6` = `v0.1.0-rc6`、
   `website/install.sh:6` = `v0.1.0-rc9`，而 `tests/test_bootstrap_installer.py:87` 要求两者逐字节相同。
   后果：从仓库跑 `bash scripts/bootstrap.sh` 的用户装到 **rc6**，从网站装的装到 **rc9**；仓库自己的回归
   门禁在干净检出上永远红一条。**修法**：在发行时把两处一起指向本次发行版本，并重新生成对应 asset。
   （B8-7 §3 已经决定"改它等于做一次发行决定"，所以本批只记录。）
2. **仓库根目录缺 `.gitattributes`。** 后果：Windows 检出时 8 个测试 + 2 个 error 全是行尾造成的，
   `scripts/bootstrap.sh` 在 bash 下**直接无法执行**（`set: pipefail / : invalid option name`），
   封存组件哈希失配。**修法**：在仓库根加 `* text=auto eol=lf`（与 `website/.gitattributes` 一致）并重新
   检出；这条不改产品代码，却能消掉 `host-boundaries` 的 8 个失败中的 7 个。
3. **官方测试入口在干净主机上缺测试依赖。** `python3 scripts/test.py` 用 `sys.executable`，干净主机上没有
   `pytest` / `httpx` / `psutil` / `PyYAML` / `python-dotenv` / `requests` / `httpx[socks]` /
   `pytest-asyncio` / `ruamel.yaml`，于是 `FAILED_SUITES` 非空。**修法**：给出可复制的依赖安装行（或 dev
   extra）并写进 `TESTING.md`，否则"回归全绿"这句话在干净主机上不成立。本批只在隔离 venv 里验证过
   `cognition-faults` 1 passed、`Hermes` 29 passed / 0 failed、`host-boundaries` 只剩 1 failure。
4. **24 小时观察必须在 RC 代码上重来。** 现在跑的是 `e471241`（不含 RC1-01/RC1-02），所以即使跑满，
   `SOAK_VERIFIED_24H` 也不覆盖被验收的提交。重来意味着放弃当前窗口（本批**没有**动它），需要 operator
   决定。
5. **旧供应商密钥的轮换（账户持有人）。** 代码路径已改成 0600 `model_env` 交接；复核当前常驻进程的命令行
   （`/proc/2728/cmdline`）里**没有**任何 `NYA02_MODEL_*` 变量，密钥不再出现在 `ps` 里。但**旧密钥的撤销
   只能在供应商后台做**，代码修复不能代替。
6. **如果发布说明要提"生产常驻"，systemd 单元必须先上真实硬件。** 本机 `systemctl is-system-running` =
   `offline`，`/run/systemd/system` 不存在，单元只是建议稿；当前常驻用的是 nohup + pidfile。

---

## 6. 必须披露的 RC 限制（逐条真实状态）

| # | 限制 | 真实状态 |
|---|---|---|
| 1 | **T12：原生生活经历没有 CORRECT 入口** | **成立**。`components/native/memory_control.py:131-133` 要求替换事件来自直接用户输入（`speaker != 'user' or source_origin != 'USER_VISIBLE_INPUT'` → `ValueError`），生活经历没有用户事件，伪造用户消息会违反 E07。已通过的只是 DELETE / 停止召回（`MEMORY_CHECK [5]`）。发布说明必须写"生活经历不可替换，只能停止召回" |
| 2 | **已被取代的版本可能拿不到 AR-1 观察证据** | **成立且是结构性的**。RC1-01 的验证走的是"owner 确认的那个版本**就是当前 head**"（`resolve_operation` + `read_head` + operation_key + content_sha256 四条）。一个**已经不再是 head** 的旧版本拿不到这份证据，于是**正确地**保持 OPEN / `UNKNOWN_EFFECT`，而不是被猜成成功。RC1-01 的真实 E14 重放里就有一条旧写入这样被留在 OPEN |
| 3 | **7 天（以及 24 小时）未完成 → 长期稳定性未验证** | **成立**。真实有效观察 8.23 小时，唯一空档 53 秒，进程仍在跑；没有满 24 小时，更没有 7 天。**不得**对外称"长测通过" |
| 4 | **systemd 单元未在真实硬件验证** | **成立**。本机 systemd `offline`；没有做过开机自启、崩溃重启、权限与依赖顺序的验收 |
| 5 | **旧供应商密钥仍需账户持有人轮换** | **成立**。代码侧已修（0600 `model_env`；当前进程命令行无密钥），但旧密钥的撤销/轮换只能由账户持有人在供应商后台完成 |
| 6 | **自主 Telegram 联系不投递** | **成立**。`autonomous_life_resident.py:269` 硬编码 `proactive_enabled=False`；T20 的 health 记录里 `proactive_enabled: false`、`autonomous_action_enabled: false`。本轮 E14 S6 只是"插件上下文 + 一次直接模型调用"，**不是** Telegram / CLI 端到端收发 |

---

## 7. 判定：CONDITIONAL_GO

**理由**

* **不是 GO**：功能完成不等于可以发。上面 §5 的 6 条里有 3 条是必须先在仓库里改掉的（安装器版本漂移、
  `.gitattributes`、测试依赖可复现），1 条必须在 RC 代码上重跑长测，1 条要账户持有人动手，1 条要真实硬件。
  另外 `python3 scripts/test.py` 在干净主机上 `FAILED_SUITES` 非空，且其中一条是**真实缺陷**而不是环境问题；
  在这个状态上发版，等于把一个"回归全绿"的假印象一起发出去。
* **不是 NO_GO**：没有发现任何不可修复、会丢数据或会让链条说谎的缺陷。T01–T20 的证据是真实的
  （真实 owner、真实模型、真实长测记录），RC1-01/RC1-02 修掉的是"记录与链条不一致"，本批又核实了
  AR-1 的边界与 T13 的口径变化并把它们写进文档；剩下的全部是**范围外或已知并已披露**的限制。
* **CONDITIONAL_GO 的含义**：可以作为 **RC（候选版）** 交付给 operator 内部验收与试用，条件是：

  1. 发布说明**逐条照抄** §6 的六条限制（尤其"长测未满 24 小时"、"T12 不可替换"、"自主 Telegram 不投递"）；
  2. 在仓库里修掉 §5 的 1、2、3 条（安装器版本对齐、加 `.gitattributes`、给出可复现的测试依赖安装行），
     再重跑一次 `python3 scripts/test.py` 并把真实的 `FAILED_SUITES` 贴出来；
  3. 在**被验收的提交**上重开 24 小时观察（现窗口是旧代码），满 24 小时前不得改 `SOAK_VERIFIED`；
  4. 账户持有人完成旧密钥轮换；若发布说明要提"生产常驻"，先在真实硬件验 systemd 单元。
  任何一条不满足，就退回 **NO_GO**（尤其是条件 3：如果 operator 的标准是"没有在本 RC 代码上跑满 24 小时
  就不发"，那么今天就是 NO_GO）。
* **不得**把本判定读成"可以建标签/建 Release/合 `main`/切生产"：这些都需要单独授权，本批一件都没做。

---

## 8. 本批没有做、也不得声称

没有创建标签或 Release；没有合 `main`；没有部署到任何真实用户实例；没有改动任何生产 profile
（`/srv/nya02/inst`、`/srv/nya02/resident`、`/srv/nya02/t20`、`~/.nyairo` 都未触碰）；没有停止或重置
`/srv/nya02/resident2`；没有轮换或改动任何凭据；没有跑 `run_isolated_acceptance.sh` / `run_world_move_e2e.sh`
（含 `pkill` / `rm -rf /srv/nya02`）；没有重跑 T16 与 isolated acceptance；没有修 §5 的六条；**真实模型调用 0 次**。
