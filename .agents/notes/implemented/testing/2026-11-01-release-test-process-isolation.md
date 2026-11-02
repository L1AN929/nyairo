# Agent Note: 发行回归按文件独立子进程

Status: implemented

## Problem

`scripts/test.py` 的 host-boundaries 用一条 `unittest discover -s tests -p test_*.py` 把
233 个文件装进同一个解释器。两个后果在这次发行回归里同时出现：

1. `tests/` 没有 `__init__.py`，而 `vendor/hermes/tests/__init__.py` 是常规包。PYTHONPATH
   里的 `.:vendor/hermes` 让 `import tests.*` 解析到 Hermes 自己的 `tests` 包，日志里 31 次
   `ModuleNotFoundError: No module named 'tests.test_one_day_life'` 由此而来。
2. 单进程共享 `sys.modules`：先导入的文件把 `phone`、`scripts` 等名字缓存成别的对象，后面的
   `import phone as phone_cli` 拿到不含 `main` 的模块，`test_phone_subject_life` 整类报
   `module 'phone' has no attribute 'main'`。

两条都不是被测代码的缺陷，但会让整个发行回归失真。

## Decision

新增 `scripts/owned_test_runner.py`：仍是 `tests/test_*.py` 全量发现、仍跑 unittest 原用例、
不 exclude / skip / deselect，只是改成每个文件一个子进程。子进程：

- `PYTHONPATH` 只放仓库根，`vendor/hermes` 留给需要它的测试自己在 `sys.path` 里按正确顺序插入；
- 每个文件一个空的 `HOME`/`HERMES_HOME` 子目录，并行时不共享落盘位置；
- 任一文件非零退出，整体非零退出，并打印 `FAILED_FILES` 与每个文件的原始输出。

路由只按文件名显式：当前只有 `test_cognition_reliability.py` 是 pytest 风格，按
`scripts/test.py` 的 cognition-faults 口径走 `python -m pytest` + `PYTHONPATH=.:vendor/hermes`；
其余全部原样走 unittest。**不再用「Ran 0 tests」回退到 pytest**——unittest 的导入错误同样
会打印 `Ran 0 tests`，回退会把真实 error 覆盖成一次可能空跑成功的 pytest。

未接收的边界：`tests/fixtures/direct_world_bridge/*`（绕正式票/gateway 的直连端口）、
`tests/fixtures/frozen_clock.py` 与 `frozen_site/sitecustomize.py`（对全 stdlib / 所有 `-c`
子进程注入时钟，没有显式 offline gate）、以及 Memory 测试里用内存 dict 冒充 AG-2 owner
持久记录的写法，都不是本 runner 的一部分，也不作为已接收的产品/测试合同。文件保留在原地，
但不再被引用；相关用例在正经验收前保持红。

**过期实验的时间边界（2026-11-01 收尾定案）**：绝对截止已经过去的六个离线实验测试
（c/f/candidate_trace/history_choice/d/e）不许靠冻结全局时钟变绿。可用接缝只有一条：
`g1_real_adoption.offline_clock()` / `enter_offline_fixture()` 在**本进程内存**里进入一个
从本批 profile 推导出来的参考时刻（截止前 1 小时）。它不读环境变量里的时间戳、不写窗口/
批/prereg 任何持久字段；同一份窗口被正式进程读到时，仍按真实墙钟判到期。父级真的要用
子进程跑 phase 时，只在 `start_run(offline=True)` 这一次实际离线调用路径构造的子进程 env
里放一个布尔标记，子进程还要同时满足 `config.offline` 与 scratch 根校验才会采纳。正式根
（非 mkdtemp）带着这个参考时刻会在任何写入之前以 `OFFLINE_CLOCK_REFUSED` 拒绝。禁止
patch 全 stdlib/sitecustomize、服务安全钟、预算 UTC、lease 或真实超时；也禁止把
`clock=offline` 这类标记写进窗口骗过后来的进程。

守卫按调用方**实际**的 offline 布尔判（2026-11-01 收尾补齐）：参考会话里再传
`offline=False` 一律在写窗口、读 prereg、解析模型配置之前以同一 `OFFLINE_CLOCK_REFUSED`
拒绝，不静默退回真实钟，也不加载真实模型配置；只有拿得到 root 的内部口
（`_write_window`/`_deadline_passed` 等）不传该参数，仍按 scratch 判定挡真实根。
负控只调最内纯守卫，或用 mkdtemp scratch + `offline=False` 把写/模型/服务边界 patch 成
raise 证明前拒；**不对正式根路径调用 `prepare`/`start_run` 这类正式入口做试探**。

`scripts/test.py` 的 host-boundaries 行改为调用它（`-j 4`）。其它 13 组与 Hermes 默认
四文件调用不变，没有新增依赖。

## Alternatives considered

**给 `tests/` 加 `__init__.py`，让它成为常规包。** 这能解决第 1 条，代价最小，但它改变的是
仓库的包结构：任何 `import tests.*` 的入口都跟着变语义，且完全不影响第 2 条（同进程
`sys.modules` 污染），`test_phone_subject_life` 仍然会红。否决。

**只把 PYTHONPATH 改成 `.:` 而不拆进程。** 能解决第 1 条，成本也很低，但 `phone` 那类污染是
执行顺序问题，单进程内无法隔离；实测单独跑 `test_phone_subject_life` 绿、混跑红。否决。

**继续用 vendor/hermes 自带的 `run_tests_parallel.py`。** 它按文件并行，但它属于 Hermes 的
测试域，PYTHONPATH 与 HOME 的口径都按 Hermes 组设置，直接拿来跑 host 组会把 `tests` 包冲突
再引回来。否决。

## Consequences

- host 组从单进程串行变成 `-j 4` 的文件级并行，回归墙钟明显下降；失败聚合与退出码语义不变。
- 每个文件拿到独立的空 HOME，过去依赖跨文件共享 HOME 的隐含顺序会暴露成真失败；这正是想要的
  信号，不是回归。
- 剩下的红是真实红（body reader、世界动作、Memory consumer、已过期实验 deadline 等），
  不被这套隔离掩盖，仍按原样失败并打印。

## 追加（2026-11-01 收尾）：life-v02 组的 SKIP 缺口与真实隔离驱动

### Problem

`scripts/test.py` 的 `life-v02` 组原先只给 `PYTHONPATH=components/life/scripts` 并直接跑
`components/check_life_v02.py`，没有 `NYA02_SUPPLY_SOCKET` / `NYA02_SUBJECT` /
`NYA02_WORKSPACE_ID` / `NYA02_ARTIFACT_GRANT`。检查脚本在缺这四个变量时打印
`SKIP: isolated Life Supply environment not configured` 并 **exit 0** —— 发行回归里它一直是
"没跑"，不是"通过"。这是覆盖缺口，不是绿。

### Decision

新增 `scripts/life_v02_isolated_check.py`（既做环境装配也做执行与判定），`scripts/test.py`
只把 `life-v02` 一行改成调用它，其它 13 组与 Hermes 不变。装配事实：

- 全新 `mkdtemp` 根；`home/`、`life-state/`、`data/` 都在这一个根下，失败保留 scratch。
- 真起 `LifeSupplyService` + `LifeSupplySocketServer`，用新 socket 路径；不 unlink 旧 socket。
- 用现成 operator grant 流程：`PROVISION_PERSONAL_WORKSPACE`(ONE_SHOT) 建 workspace，
  `COMMIT_MANAGED_ARTIFACT`(REUSABLE) 建 grant。不可用假 ID 代替；grant 值从不打印。
- 四个 `NYA02_*` 只放进**子进程 env**，父 env 不 patch；子进程 `HOME` 独立，`PYTHONPATH`
  只有 `components/life/scripts`（与旧行一致，其余路径检查脚本自己插入）。
- 判定 fail closed：`SKIP`、0 条 `ok`/`FAIL`、无 `V02_CHECK:` 终结行、子进程非 0/1，
  一律非零退出，不允许 SKIP 冒充通过。
- owner 正规停止：`shutdown` → `server_close` → `drain(10)` → `service.close`；不裸 unlink、
  不 shutil.rmtree，子进程 `subprocess.run` 同步等待。

脚本替身边界不变：检查里的认知是 `TestDoubleCognition`，内容是 `IdentityWriter`/`RacingWriter`，
0 产品模型、0 网络。

### 当时的红（历史，已由场景隔离修复）

真实装配下检查**确实执行**（52 条具名断言，1–6 节通过），但在第 7 节 CONFLICT 起被
`SAME_STATE_ALREADY_CONSIDERED` 挡住：第 6 节 NO_CHANGE 在 `life-state/life_reconsideration.json`
记下 `outcome=ALREADY_DONE` 的 situation（TTL 21600），第 7/8 节从同一 head 进入，situation
指纹完全相同，于是 `reconsideration_block` 在调用 writer 之前就判定"同状态已考虑过"，
`RacingWriter` 根本没被调用，进而第 8 节拿不到 action、`check_life_v02.py` 提前 `return 1`、
没有 `V02_CHECK:` 终结行。当时**未改 `components/check_life_v02.py`**（本单冻结），STOP 交裁定。

### 否决：删 `life_reconsideration.json` 再跑（本单明确不采纳）

当时的止损建议是"在 `build()` 里 unlink 预算文件旁边再 unlink 重考账本"，理由是最小改动、
只动测试侧、不改断言也不改生产规则。这条**不采纳**：重考账本本身就是被测的生产行为，
"同一状态不重复考虑"正是它该有的效果；删掉账本让第 7/8 节重新开口，等于用删除历史来绕过
静默门，失败信号被消灭而不是被解释。预算文件那次 unlink 同理一并撤掉。

### Decision（2026-11-01，本人裁定"修场景隔离"）

`components/check_life_v02.py` 改为**按场景分状态根**，不删任何账本：

- 互相独立的场景（[1] 空 owner、[6] NO_CHANGE、[7] CONFLICT、[8] UNKNOWN、[9] 预算、[9] off）
  各用 `install_root.parent/life-state-<name>` 一个自己的根；这些根在本次 scratch 根下
  （driver 的 mkdtemp），不自动清理、不 rmtree，失败留在盘上。
- **连续性链只用一个根**：`NYA02_LIFE_STATE`（即 `install_root`）就是 [3]→[4] 的根——同一作品、
  同一 Activity、同一本预算账本与原定 `MAX_ROUNDS` 上限，不退款不重置；[11]→[12] 另用一个根，
  pending handoff、原 AR-0 action 与其 settlement 在重启前后必须是同一身份。
- [8] 的 UNKNOWN 与 reconcile 天然同根：对账问的是**那一个**原操作，不是另起一件。
- [10] 读的是 [3]→[4] 这条真走过 AR-0 的链的 `life_step_journal.jsonl`，不是别的场景的空目录。
- [11] 里"Activity 没回滚"的断言改读**本轮自己采纳的** `activity_ref`（同一期望、只是换引用目标），
  因为该节现在有自己的根；[12] 的 evidence/controls 也随之落在重启链的根里。

原 83 条断言全部保留（只允许改引用路径/装配目标）。落盘格式、CLI、生产源码、Memory 准入门均未改。

### 实测（2026-11-01 21:49 CST，WSL `/tmp/rc104-venv`）

命令 `python -B scripts/life_v02_isolated_check.py`：`EXIT=0`，`V02_CHECK: PASS`，
`LIFE_V02_ISOLATED: EXECUTED_PASS (exit=0, checks=83)`，owner `drained=True / inflight=0`。
scratch `/tmp/nyairo-life-v02-iso-_tc69n0d` 保留。第 10 节读到 2 条 step receipt（连续链自己的根）。
前两次红（52 断言 exit 3、无终结行）作为失败史保留，不倒写成一次通过。

### 未验界限

- 这版仍**没有**证明自然模型采用、Memory 准入放宽、整机回滚新鲜或完整 V-C；模型/内容仍是脚本替身。
- 只在 WSL `/tmp/rc104-venv` 下跑过；Windows 原生未跑。
- 若日后连续链自己撞上 `SAME_STATE_ALREADY_CONSIDERED`，那是要具名报告的真失败，不许再靠
  删账本/解除静默绕过。
