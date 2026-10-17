# W04 回执：世界后端收口验收（即时模式 pause 竞态修复 + 隔离全量回归 + 真实时钟烟测）

- 施工单：`D:\NYAIRO-v0.3-W04-后端收口验收任务单.md`
- 执行模型：国际版 WorkBuddy DeepSeek `custom/global:deepseek-v4.1-flash`（无子代理委派）
- 交付树：`D:\nyairo-v03-backend`；分支 `work/v03-w01-navigation`；HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837`（**未移动**）
- 状态：已修复 + 已离线复测 + 已做真实时钟烟测；**未 commit、未 push、未部署、未安装/启用任何服务、未调用真实模型**
- 验收环境：WSL Ubuntu + Python 3.12.3（POSIX `fcntl`、tzdata）；scratch 隔离 venv；`PYTHONDONTWRITEBYTECODE=1`
- 结论限定：**世界后端首批收口（W04 范围）通过**。不宣称完整 v0.3 发布、生产稳定、自主生活完成，也不宣称 24h soak 通过。

## 1. 开工前置与计数修正

开工快照（全部在会话 scratch `C:\Users\<user>\.pi-desktop\scratch\966ed2b0-27ad-4c2c-a4b3-9021a8121e37`）：
`w04_start_status.txt`、`w04_start_head.txt`、`w04_start_manifest.txt`（18 项：路径+字节+SHA256）、
`w04_backup/world_move_tool.py.orig`（`8e54bd4c46fc8e8e…`）、`w04_backup/world_journey.py.orig`（`9194433ffc34446c…`）。
开工时 `git diff --check` exit 0，HEAD/分支与施工单一致。

**开工实测 = 8 个 tracked 修改 + 10 个 untracked（逐文件，不凭口算）**：

| 类别 | 文件 |
| --- | --- |
| tracked M（8） | `chiyo_bundle/world_move_tool.py`、`components/world/scripts/world_action_resolver.py`、`components/world/scripts/world_living_skeleton.py`、`components/world/scripts/world_runtime_data.py`、`components/world/scripts/world_travel_delivery.py`、`components/world/service/world_body_gateway_api.py`、`scripts/life_heartbeat.py`、`tests/test_world_move_tool.py` |
| untracked ??（10） | 3 个新产品模块 `chiyo_bundle/world_control.py` / `world_journey.py` / `world_runner.py`；3 个新测试 `tests/test_world_journey.py` / `test_world_move_tool_navigation.py` / `test_world_runner.py`；3 份历史回执 `docs/engineering/v03-w01-result.md` / `v03-w02-result.md` / `v03-w03-result.md`；1 份说明 `docs/engineering/v03-runner-usage.md` |

**计数修正（只写在本报告，W01–W03 回执字节未改动）**：W03 回执 §1 结尾把结束状态写成
“8 个 tracked 修改 + 9 个 untracked”，**实测是 8 + 10**（上表），少算 1 个 untracked。
差额只能定位为“历史回执 + 使用说明”那一组的计数错，无法进一步指认具体哪一项（W03 未逐文件列）。
W03 §2 的“W03 自身增量 5 个新增”与实测不冲突：它描述的是 W03 自己的增量，不是当时的全树 dirty 集合。

本单结束时的 owned delta = 19 项（8 M + 11 ??，新增 `tests/test_world_move_pause_race.py`），
再加本回执共 20 项。冻结清单见 §7。

## 2. 本单相对 W03 的修改（行级）

| 文件 | 改动 | 字节/SHA 变化 |
| --- | --- | --- |
| `chiyo_bundle/world_move_tool.py` | L425–447：即时多段循环改为**逐段取 journey 锁 + 锁内重读暂停闸门** | 23204 → 24530 字节；`8e54bd4c…` → `510c301e…` |
| `tests/test_world_move_pause_race.py` | **新增**，212 行 / 3 项确定性竞态回归（`@posix_only`） | 新文件；`f2843f6a…` |
| `docs/engineering/v03-runner-usage.md` | L90–96 新增“即时模式逐段提交边界”真实语义说明 | 11807 → 12261 字节；`f72908e7…` |

其余 16 项（含 `world_journey.py`、`world_control.py`、`world_runner.py`、`world_living_skeleton.py`、
`world_travel_delivery.py`、`world_runtime_data.py`、`world_action_resolver.py`、`world_body_gateway_api.py`、
`scripts/life_heartbeat.py`、`tests/test_world_move_tool.py`、`tests/test_world_journey.py`、
`tests/test_world_move_tool_navigation.py`、`tests/test_world_runner.py`、三份历史回执）
**本单未做任何字节改动**（§7 哈希核对为证）。

### 2.1 修复后的即时路径语义

```python
committed = []
for step in route:
    with journey.journey_lock(home):          # 与 set_pause 同一把锁
        gate = _control_module().gate(home)   # 锁内重读闸门
        if gate["blocked"]:
            return _result(False, "paused" if paused else "control_blocked", ...,
                           reason_code=gate["reason_code"], paused=paused,
                           location_id=committed[-1] if committed else current,
                           committed_route=list(committed), route=route)
        moved = wls.apply_move_to(step, **options)
    ...
```

- 闸门与提交在**同一把 journey 锁**内，因此 `pause` 返回成功之后不可能再提交新段；
  若某段提交正在进行，`pause` 等它结束再确认（至多等“当前这一段”）。
- 闸门阻塞时返回 `ok=False`、`status="paused"`/`"control_blocked"`、
  `reason_code` = `JOURNEY_PAUSED`/`CONTROL_STATE_CORRUPT`、
  `location_id` = 最后已确认位置、`committed_route`、`route`，**绝不虚报 `arrived`**。
- 锁序仍是 `journey → world → travel`：不在持 owner 锁时取旅程锁；不把 `move_handler` 整体套非重入锁，
  也不在锁内递归调用 `accept_journey`/`advance_journey`，因此无死锁、无锁序反转。

## 3. 竞态复现与负对照

### 3.1 根因（父代理已定位，本单实读确认）

`world_move_tool.py` 原 L344–357 单独读 `world_control.gate(home)`（**不在 journey 锁内**）；
L420–424 计时路径交给 `accept_journey`（有提交边界闸门）；
**L425–438 即时路径逐段调用 `wls.apply_move_to`，既无 journey 锁也无闸门**。
于是真实时间序列“即时 move 读到放行 → 另一线程 pause 写入并返回成功 → 即时 move 继续提交”
在旧树上会**在 pause 确认后继续提交整条路线并虚报到达**。

### 3.2 两类确定性回归（`tests/test_world_move_pause_race.py`，3 项）

用**事件/屏障**而非 `sleep` 猜时序：seam 为 `mock.patch.object(world_journey, "journey_lock", observed_lock)`，
在第 N 次获取 journey 锁之前先让 helper 线程用**原始**锁完成真实 pause / 写坏控制文件。

| 测试 | 覆盖 |
| --- | --- |
| `test_first_segment_not_committed_after_pause` | 首段前竞态（seam=1）：pause 确认后第一段也不得提交 |
| `test_mid_route_pause_stops_after_last_confirmed_segment` | 多段之间竞态（seam=2）：断言 `acquisitions==2`、`applied==[s1]`、位置真实停在 s1 |
| `test_corrupt_control_between_gate_and_commit_fails_closed` | 闸门与提交之间控制状态损坏 → fail closed |

结果：**3/3 OK**（4.881s / 0.525s，两次全量回归均 3/3）。

### 3.3 负对照（scratch 整树副本，非原地回退）

副本 `<scratch>\w04-neg\repo`（`robocopy` 排除 `.git`/`__pycache__`，12463 文件），
只把 `chiyo_bundle/world_move_tool.py` 还原为 `.orig` **原字节**；`diff -rq` 证明副本与工作树
**只差这一个文件**（其余依赖、`world_journey.py`、`world_control.py` 全部保留，失败不可能是 ImportError）。

- 副本里新测试 **3 项全 FAIL**，且是**断言行为失败**（`applied` 为全部 3 段 / `acquisitions==0`），
  无 ImportError、无文件缺失、无死锁超时，~4s 结束。日志 `<scratch>\w04_neg_race.txt`。

### 3.4 探索脚本 `w04_neg_window.py` 的 1 项 FAIL —— 如实说明

`<scratch>\w04_neg_window.py` 是**本单为精确定位时序窗口而写的 scratch 探索脚本**（不在产品树、不属于交付测试）：

- 它的唯一断言是 `assertTrue(marker["pause_confirmed"])`，即断言“**pause 在第一次提交之前就被确认**”——
  这正是**未修复树**的时序。
- 在**未修复**树上它通过，并同时暴露缺陷（`<scratch>\w04_neg_window.txt`）：
  `pause_confirmed_before_first_commit=true`，
  `applied_after_pause_confirmed=["beach","bookstore","convenience_store"]`，
  `tool_status="arrived"`、`tool_ok=true`、最终位置 `convenience_store`。
- 在**修复后**树上它 **1 项 FAIL**（`<scratch>\w04_fixed_window.txt`），因为修复后 `pause` 要等当前段提交完成才确认，
  所以 `pause_confirmed_before_first_commit=false`；同一次运行的实测数据恰好证明修复生效：
  `applied_after_pause_confirmed=[]`、`final_position="beach"`、`tool_status="paused"`、`tool_ok=false`。

**结论（不隐去、不算通过）**：这 1 项 FAIL 是**该探索脚本断言的是旧时序**所导致的**预期失败**，
它**不是**产品缺陷、**也不是**验收项；它**没有**被计入任何“通过”数字。
权威回归是 §3.2 的 3 项产品测试（修复后 3/3 OK、对 `.orig` 字节树 3/3 行为失败）。

## 4. 隔离解释器、依赖与一次环境修正

- scratch venv：`<scratch>\w04venv`，Python **3.12.3**，pip 24.0；`pip freeze` = `psutil==6.1.1`、`PyYAML==6.0.2`。
  两者都由仓库 `requirements-dev.txt`（测试专用依赖）声明，来自公开 PyPI。
  **“获取依赖需要网络”与“产品运行不调用网络/模型”是两件事**，分别如实报告：产品链路本身未联网、未调模型。
- 未向全局 Python 安装、未跑 `scripts/install.sh`、未升级 vendor 依赖。
- 临时目录：`TMPDIR=/tmp/w04tmp`（**原生 Linux 短路径**）。

**环境修正（必须记录，否则会误判为产品回归）**：第一轮全量回归把 `TMPDIR` 指向
`/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-…/w04tmp`（Windows 长路径），出现
`failures=1, errors=14`（`components/world/service` 259 项）与整仓 `failures=3, errors=10`。
逐条读 traceback 后归因**全部是环境**，与本单改动无关：

| 现象 | 真实原因 |
| --- | --- |
| `OSError: AF_UNIX path too long`（14 项） | `AF_UNIX sun_path` 上限 107 字节，`/mnt/c/...` 长前缀超限 |
| `AssertionError: 511 != 384` / `511 != 448`（0o777 vs 0o600/0o700） | DrvFs（`/mnt/c`、`/mnt/d`）不支持 POSIX 权限位，`chmod` 无效 |
| `OSError: [Errno 95] Operation not supported` | 同上，DrvFs 不支持该文件操作 |
| `ModuleNotFoundError: No module named 'yaml'`（3 个模块导入失败） | 隔离 venv 未装 PyYAML（`vendor/hermes/hermes_cli/config.py:24`） |

改用短路径 + 补装 PyYAML 后**同一棵最终树全部转绿**（§5）。**第一轮结果不用于任何结论**，
只作为环境归因证据保留（`<scratch>\w04_final_regression.txt`）；结论性证据是
`<scratch>\w04_final_regression2.txt`（单次连续 8 步跑完的同一棵树）。

## 5. 最终树全量回归：命令 / 退出码 / 计数

脚本 `<scratch>\w04_final_regression2.sh`，原始输出 `<scratch>\w04_final_regression2.txt`。
解释器：`<scratch>\w04venv/bin/python`（3.12.3，uid 0）；`PYTHONDONTWRITEBYTECODE=1`；`TMPDIR=/tmp/w04tmp`；
`git_head=7f09d26df64e054faf51ba35ad85c64f73a5a837`、`git_branch=work/v03-w01-navigation`；
`started_utc=2026-10-15T12:30:38Z`、`finished_utc=2026-10-15T12:33:29Z`。

| # | 命令 | 结果 | 退出码 |
| --- | --- | --- | --- |
| 1 | `cd tests && python -m unittest discover -s . -p test_world_journey.py` | **28 tests / OK** | 0 |
| 2 | `cd tests && … -p test_world_move_tool.py` | **16 tests / OK** | 0 |
| 3 | `cd tests && … -p test_world_move_tool_navigation.py` | **14 tests / OK** | 0 |
| 4 | `cd tests && … -p test_world_runner.py` | **26 tests / OK** | 0 |
| 5 | `cd tests && … -p test_world_move_pause_race.py`（本单新增） | **3 tests / OK** | 0 |
| 6 | `python -m unittest components.worldgen.tests.test_v04_navigation test_edited_world_runtime test_runtime_wiring` | **24 tests / OK** | 0 |
| 7 | `cd components/world/service && python -m unittest discover -s . -p 'test_*.py'` | **259 tests / OK** | 0 |
| 8 | `cd tests && python -m unittest discover -s . -p 'test_*.py'`（整仓） | **161 tests / OK** | 0 |

整仓逐项清单（`<scratch>\w04_whole_repo_verbose.txt`，`-v`，exit 0）：
`test_world_journey` 28、`test_world_runner` 26、`test_world_move_tool` 16、
`test_world_move_tool_navigation` 14、`test_update` **13（全 ok）**、`test_context_safety` 12、
`test_privacy_security` 9、`test_presence_integration` 9、`test_bootstrap_installer` 7、
`test_platform_scope` 4、`test_filesystem_security` 4、`test_world_move_pause_race` 3、
`test_sqlite_safety` 3、`test_release_metadata` 3、`test_instance_lifecycle` 3、
`test_runtime_health` 2、`test_profile_install` 2、`test_idle_writer` 2（合计 161）。

**psutil 归因复测（施工单 §4 要求“真正复测，而非只引用旧归因”）**：
W03 记录的 9 个 `test_update.UpdateTransactions.*` error 在装上 `psutil==6.1.1` 后**全部消失**，
该模块 **13/13 ok**（含真实 upgrade/rollback 事务日志）。因此旧归因成立且已实测确认。
整仓计数 158（W03）→ **161**：差异来自原先在 `setUp` 里因缺 psutil 提前失败的用例现在跑到底，
以及 `test_update.py:182` 的 `subTest(entry=…)` 循环在成功路径上才展开；W03 未用 `-v`，其逐项清单已不可重建，
故只报实测值 161/OK，不反推 W03 的逐项构成。
`requests`/`httpx` 未装 → 若干**可选**工具模块打印导入告警（`Could not import tool module …`），
不是测试失败，也不影响退出码。

## 6. 真实时钟隔离烟测（约 10 分钟，**不是** soak）

夹具由 worldgen 正常校验路径生成（**未改产品默认耗时**）：seed 929711 / `coastal_town`，
道路图 `home → beach(300s) → bookstore(200s) → convenience_store(150s)`（三段合计 650s）。
世界包与世界实例都在 scratch：`<scratch>\w04-smoke\world-package`、`<scratch>\w04-smoke\world-home`。
产品用**真实 UTC now + monotonic 等待**：无假 clock、无 `--now`、无 sleep 猴子补丁；
CLI 前台子进程 `scripts/life_heartbeat.py run --interval 5`（`--env` 显式给 gate）。

**最终一次运行（结论性）**：`<scratch>\w04_smoke_evidence.json` / `w04_smoke.txt` / `w04_smoke_events.jsonl`

- 起止：**2026-10-15T13:07:38Z → 13:20:19Z**，monotonic 时长 **760.841s**（≥600s，≤900s）
- 子进程身份：`first pid=7141`、`restart pid=7170`，两次都以 **rc=0** 正常退出（SIGTERM，只停本轮创建的进程）
- 检查项：**18/18 PASS**（`verdict=PASS`）
- 时间线（全部真实 UTC）：journey 创建/首段出发 13:07:41Z（eta 13:12:41Z，=300s）→
  **在途 pause 确认 13:08:28Z**（首段已出发 47s）→ 首段真实到达确认 **13:12:44Z** →
  +40s（≥8 个真实 tick）后仍是 `travels=1`、位置 `beach`、`paused=true`、第 2/3 段仍 `pending` →
  停 runner（rc=0）+ 只读零写入窗口 → 重启 runner 后 40s 仍 `paused`、`travels=1` →
  **resume 13:14:24Z** → 第 2 段 13:14:25Z 出发（**pause 确认之后**）、13:17:45Z 确认 →
  第 3 段 13:17:45Z 出发、13:20:15Z 确认 → 结束
- 终态：位置 `convenience_store`、`world_state.revision=4`、travel 记录 3 条全 `ARRIVED`、
  `execution_id` 三条互不相同且与 journey 的 segment 一一对应（`…-0`/`…-1`/`…-2`）、
  journey `status="completed"` 且三段 `confirmed`
- **零写入证明**：在 runner 已停 + `paused` + 无到期推进的静止窗口，前后两次 `status` 查询
  （退出码均 0）之间，`world_state.json`、`world_travel_delivery.json`、`world_journey.json`、
  `world_control.json`（以及不存在的 `intended_target.json`）**字节与 mtime 全部不变**；
  runner 运行中（paused）的同一窗口只有 runner 自己的 `world_runner.json` 合法变化，其余不变。
- 旧 W02 意图文件 `intended_target.json` 全程不存在（未被新链路写入）。

**三轮烟测如实并列（不隐去失败）**：

| 轮 | 时长 | 结果 | 原因 |
| --- | --- | --- | --- |
| 1 | 768.689s | NOT_PASS 13/17 | **烟测脚本自身断言写错**（pause 结果在 `payload["control"]["paused"]`；`segments` 是整条路线计划而非“已提交段数”；completed 旅程仍保留在 `active`）。产品行为实测正确 |
| 2 | 762.512s | NOT_PASS 17/18 | 同上修好后仍余 1 项：脚本在 `resume` 后立刻读 `segments()[1]`，此时该段仍是 `pending`、`started_at=null` |
| 3 | **760.841s** | **PASS 18/18** | 断言改为“等到该段真的离开 `pending` 再读” |

三轮的**产品侧观测一致**（3 段全到达、execution_id 不重复、pause 期间不续段、零写入），
差异只在脚本断言；但**结论只取第 3 轮**，前两轮不拼接、不用于“通过”。

**边界（必须同时声明）**：这是 **10 分钟真实时钟烟测**，不是 `SOAK_VERIFIED`，
不代替 24 小时最终候选长测与线上验收；未安装 systemd/计划任务/daemon。

## 7. 最终树证据与 manifest 核对

- 冻结清单：`<scratch>\w04_manifest_frozen.txt`，**19 项**（8 tracked M + 11 untracked，含新测试），
  每项 `路径 + 字节数 + SHA256`。
- 核对脚本 `<scratch>\w04_verify_manifest.sh`；**测试结束后的核对结果**：
  `<scratch>\w04_manifest_verify_after_smoke.txt` → `manifest entries=19 mismatches=0`、`MANIFEST_VERIFY=PASS`。
  即**产品/测试 owned delta 在冻结后零变化**，最终回归与烟测都对应同一棵树。
- 历史回执哈希未变：`v03-w01-result.md 6153d03e…`、`v03-w02-result.md 20316d31…`、`v03-w03-result.md 16fa847c…`。
- `git status --porcelain` 与冻结清单逐条一致（8 M + 11 ??），**无临时产物、无 `__pycache__` 残留**；
  `git diff --check` exit 0；HEAD `7f09d26d…`、分支 `work/v03-w01-navigation` 未移动。
- 本回执（`docs/engineering/v03-w04-result.md`）为结束后新增的第 20 项 untracked，按施工单不计入循环自哈希。

## 8. 未完成项与已知边界

1. **无 24h soak、无长稳结论**：本单只做 760.841s 真实时钟烟测；常驻循环的自动化覆盖仍以注入 clock 为主。
2. 原生 Windows 缺 `fcntl`，只能只读 `status`（lease 证据 `unknown`）——W03 边界未变。
3. `pause` 只拦“新段”，不撤销已在途的物理移动；位置与到达时间仍由 owner 投影决定。
4. 控制状态损坏只能人工处理（未新增 cancel / 清空 / 自动恢复 API）。
5. 整仓 `tests/` 需要 `requirements-dev.txt` 的依赖（`psutil`、`PyYAML`）；未装 `requests`/`httpx`
   会产生可选工具模块导入告警（非失败）。
6. 第一轮全量回归的 `TMPDIR` 长路径结果（14 errors + 1 failure）保留为环境归因证据，
   不作为验收结论；正确环境下的结论见 §5。
7. 未 commit / push / PR / 部署，未安装服务，未开始 W05、未做配置密钥或新功能。

## 9. 范围外写入

- 工作区（`D:\nyairo-v03-backend`）内本单只写 3 个文件：
  `chiyo_bundle/world_move_tool.py`（竞态修复）、`tests/test_world_move_pause_race.py`（新增回归）、
  `docs/engineering/v03-runner-usage.md`（即时模式语义补充），以及本回执 `docs/engineering/v03-w04-result.md`。
- **未**修改 W01/W02/W03 回执字节，**未**删除任何用户文件，
  **未**改动全局 Python/依赖/真实 profile/密钥/供应商，**未**调用真实模型。
- venv、负对照副本、烟测世界与 world_home、日志、事件 JSONL、快照、manifest 全部在会话 scratch
  （`<scratch>\w04venv`、`w04-neg`、`w04-smoke`、`w04_smoke*.{txt,json,jsonl}`、`w04_*_regression*.txt`、
  `w04_manifest_frozen.txt`、`w04_manifest_verify_after_smoke.txt` 等）。
