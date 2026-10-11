# 验收与复测

下列 R17 / R16 数字是相应历史候选的验收记录，不是当前 main 分支、每次文档更新或 GitHub Actions 的最新结果。当前版本与发行标签有差异时，分别核对提交、清单和实际运行结果。

最新 R17（2026-10-04）：3,718 文件，**45,071 通过、0 失败、440 条件跳过**，1414.6 秒，8 并发、900 秒文件上限、关闭自动重试。下列早期过程只保留为历史说明，其中 R16 独立结果不可核验。

R17 同时重新执行双 Python 千代组件与每版 36 项宿主边界，四个命令的真实插件发现与完整网关路径、陌生 DM 拒绝、旧代码负对照、41 文件补丁生命周期、F/E9 静态检查通过。17 段 Bash 示例语法与实际会话 key 示例通过；作者确认自然 Telegram `/chiyo_status` 正常返回全部模块状态。

源 ZIP 无 Git 元数据；两次验收环境准备失败的日志保留。最后完整运行在隔离、真实 Git 元数据的测试目录中完成，不把该元数据打入发行包。

R16 独立完整运行未核验：原 parent_R16_full 条目与 R17 逐字段相同，原始 R16 日志未包含在仓库中。重复数量、耗时与摘要已撤下，不再将它作为独立通过证据。

这不代表所有平台和所有外部依赖均已验证。跳过用例按上游平台/依赖条件保留，没有为修复本轮失败添加无条件跳过。原始 R6 有 25 失败、2 个重试后通过的文件，记录保留；R7 仍有 1 项进程通知失败，R8 仍有 3 项夹具失败；R9 因提供方就绪夹具失败中止，未捏造完整统计；R10 有 11 项失败和 1 个文件超时，目录与网络夹具修复后重新冻结复测。R11 的更新夹具还有 4 项失败，R12 有 1 项看板锁测试受建表耗时影响，保留原始记录。R13 使用受控单调时钟验证真实锁的超时期限，并检查真实建表结果和线程清理，未放宽产品超时。R13 又出现定时任务的 2 项墙钟断言失败后中止，未生成完整统计；R14 以真实阻塞任务和显式释放证明异步返回，并用 finally 清理真实线程池。R14 完整运行仍有 1 项独立房间就绪失败，日志保留；R15 使用既有就绪谓词、生产租期与 finally 清理，保留两个房间的运行/完成断言。R15 的压缩引擎隔离夹具在准备完成前触发超时后中止，未生成全量统计；R16 使用真实引擎入口事件，把准备总上限与 0.6 秒引擎空闲预算分开，并验证工作线程退出。最终完整复测并发为 8，禁止自动重试。不能把后续修复改写成旧运行已通过。

未改动组件的 R7 源码已在 Python 3.11.15 和 3.13.5 各执行 453 项组件测试：452 通过，1 项需要 root 的跨 UID 检查跳过；哈希与最终组件一致。R8 在香港对全部组件再复测，并在两版 Python 复测 35 项宿主边界、认知故障与各 185 项进程/通知检查（4 项平台条件跳过）。同一源码的隔离 root 补充 9 项 socket 检查已通过。每版还通过真实 Life 装配、35 项宿主边界、1 组多故障认知检查、29 项受影响 Hermes 检查。Alpha A–E 通过，F 为 PARTIAL，N8 尚未实现。

香港标准 Hermes AIAgent 的真实模型验收通过 4 项生活/世界/资源/Shadow 检查及 8 项记忆检查：跨会话召回、纠正、停止召回、不伪造平台送达证据均验证。模型检查使用私有隔离配置，没有向真实聊天对象发送测试消息。独立 Telegram 的自然收发与几天使用是另外的体验验收。

前序 19 个修复相关测试文件集中回归：659 通过、0 失败、29 跳过。最终网关两处夹具修复另执行 65 项测试及连续 5 轮无重试回归，均通过。提供方就绪竞态的 4 项问题文件连续 10 轮通过。最后 8 个目录/DNS/安装器/异步夹具文件使用官方逐文件执行器复测，173 通过、0 失败、14 平台条件跳过。最终变更的 14 个测试文件在 Python 3.11 分别完成 242 项、47 项及看板锁 2 项、定时任务 10 项通过，共 301 通过、0 失败、14 跳过。看板锁测试在两版 Python 通过，并连续 10 轮通过；恢复无期限阻塞锁的负对照按预期失败。定时任务问题文件也在两版 Python 通过、连续 10 轮通过；强制同步等待的负对照按预期失败。最后房间运行器的 43 项用例在 Python 3.11 重新验证，并在 Python 3.13 连续 5 轮通过。压缩隔离的 4 项用例也在 Python 3.11 重新验证、Python 3.13 连续 10 轮通过。补丁应用/重复应用/回滚/重复回滚/再应用全部通过。45 个组件模块从隔离源码导入，路径验证通过；新增装配代码 F/E9 静态检查通过。

后台进程通知与线程登记修复还通过两节点旧代码负对照和连续 10 轮回归，另有 40 轮多线程同时退出/启用通知检查，无漏发或重复。双 Python 另执行真实进程与网关通知测试。

更新编排的 47 项问题文件另连续 5 轮通过，保留模块清理、重启和库存的专门回归。详细数字、命令、源码摘要与日志 SHA256 见 `TEST_EVIDENCE.json`；补丁说明见 `HERMES_FIXES.md`。原始日志含大量环境细节，留在私有验收目录，未混入开源源码包。

## 本地复测

```bash
bash scripts/install.sh
cd vendor/hermes
uv sync --frozen --python 3.13 --extra all --extra dev --extra anthropic --extra mistral --extra fal --extra modal --extra daytona --extra hindsight --extra parallel-web
cd ../..
vendor/hermes/.venv/bin/python scripts/test.py
vendor/hermes/.venv/bin/python scripts/test_alpha.py
cd vendor/hermes
bash scripts/run_tests.sh -j 8 --file-timeout 900 --file-retries 0
```

安装 git、uv、Node.js 与 ripgrep；本轮 Node.js 22.19.0、ripgrep 15.1.0。源码 ZIP 不包含 Git 元数据。上游 update 类测试需要 Git 仓库，本轮只在隔离验收目录创建测试用 Git 历史；复测也需准备独立 Git 夹具，不能在个人运行目录里模拟更新。

2026-10-04 核对实际工作流后，GitHub 当前只有 Pages 网站构建与部署，运行时功能 CI 模板仍在 `ci/templates/`，尚未启用。网站发布成功不能代表组件、宿主、真实模型或平台测试通过。完整宿主、真实模型和真实平台需要分别验证；最终 ZIP 的冷安装与完整性按其实际哈希关联。

## 测试依赖与运行环境（RC1-04）

`python3 scripts/test.py` 用 `sys.executable` 逐个套件地跑，所以"装了哪些包"直接决定结果是绿还是红。干净主机上缺 `pytest` / `httpx` / `psutil` / `PyYAML` / `python-dotenv` / `requests` / `pytest-asyncio` / `ruamel.yaml` 时，`cognition-faults`、`Hermes`、`host-boundaries` 会因为没有模块而失败——那是缺依赖，不是产品缺陷。

依赖清单在仓库根的 `requirements-dev.txt`，**只用于测试**，不是任何发行实例的运行时依赖。安装与运行：

```bash
python3 -m venv .venv-dev
.venv-dev/bin/python -m pip install -r requirements-dev.txt
.venv-dev/bin/python scripts/test.py            # 官方入口；--full-hermes 才跑完整 vendor 套件
```

`scripts/test.py` 按顺序打印 `SUITE <名字>`，最后一行 `FAILED_SUITES [...]` 是唯一结论：**列表为空才算全绿**，非空时把列出来的名字当成失败套件。它给每个套件准备一个临时 HOME，不读也不写 `~/.nyairo`。

环境要求分三档：

| 档 | 需要什么 | 覆盖哪些套件 |
| --- | --- | --- |
| 任何平台 | 只要上面的依赖 | `closeout`、`native`、`memory`、`supply`、`world`、`life`、`life-v02`、`life-memory-v02`、`life-b7`、`life-b8`、`life-b8-causal`、`life-b8-6a` |
| Linux / WSL | 真 Unix 语义：`runuser`、`pgrep`、`/proc`、POSIX 权限位、`bash`。安装器与文件系统安全用例在 Windows 上会跳过或直接失败 | `host-boundaries`、`cognition-faults` |
| 隔离实例 | 一条真实 owner 链：`/srv/nya02` 下的 Supply socket、World 服务，外加该实例自己的 subject / workspace / artifact grant / Memory 数据库。**真实长测实例在跑的时候不要执行** `run_isolated_acceptance.sh` / `run_world_instance.sh` / `run_world_move_e2e.sh`：它们会 `pkill` 共享 owner 并 `rm -rf /srv/nya02` | `components/life/tests/v02/` 下的 T16 / T20 / B8 端到端脚本 |

**真实模型调用**只有两条线需要：E13 的"没有用户消息也能自主考虑"，以及 B8-6A 的长测。其余套件全部走受控替身或受控时钟，不需要真实供应商密钥。要跑真实模型，先确认配额，密钥只放在 owner 的 0600 配置文件或进程环境变量里，不要写进仓库。

**长测标签的读法**：`summary()` 只报告时长阈值**是否到达**（`SOAK_THRESHOLD_REACHED_24H` / `SOAK_THRESHOLD_REACHED_7D`），它**不**能自己判定验收通过。`SOAK_VERIFIED` 是复核过 journal、进程连续性和 owner 结果之后由人写下的结论，口径见下面的 E13 表。

## rc5 本次复测

本次源码修复的实际命令、结果和日志摘要记录在 `PRIVACY_TEST_EVIDENCE.json`，修复范围与保留边界见 `PRIVACY_REVIEW.md`。历史 45,071 记录不会被改写成本次新运行的结果；本次完成 534 项计数测试（504 项组件/边界 unittest、1 项认知故障测试、29 项官方宿主接线测试），另有真实 Life 装配检查；Alpha 五个脚本通过，N8 相关脚本保留 PARTIAL。完整宿主复跑因可选依赖和测试机资源压力没有完成，不宣称新一轮全量通过，也未发送真实 Telegram 测试消息。

测试中仍观察到旧记忆组件的 SQLite ResourceWarning、Supply 的 fork 警告，以及认知故障注入预期的线程异常警告；通过数量不等于零警告或所有技术债已清除。四项负对照在旧代码下失败，新代码下通过；普通账号实际安装与公开下载复核分开记录。

## 2026-10-04 引导安装器复核

新增 `scripts/bootstrap.sh`，网站 `/install.sh` 发布相同内容。安装器固定下载 `v0.1.0-rc5` 的公开源码归档，SHA256 为 `58d4272f4bb61fea8785789b181471e0826096562955dd1bee26401a776834b8`；不修改已有标签和 ZIP。

- Linux 普通账号、空 HOME、PATH 仅系统工具，无预装 uv：自动下载 uv、Python 3.13.16、rc5 和依赖，创建个人配置及 `nyairo` 入口，退出码 0。安装前后清单均 valid，12,195 个发行文件无变更或缺失。
- Windows WSL / Ubuntu 普通账号、独立空 HOME、无预装 uv：实际完成首次下载和安装；最终脚本再次运行退出码 0，`nyairo --version` 正常。个人配置权限 600，入口权限 700，本地授权 UID 与安装账号一致。
- Linux PTY 中通过 `curl | bash` 入口打开原生模型设置，输入本地测试服务的地址、占位密钥与模型并保存。安装出的 `nyairo -z` 经实际 HTTP 模型路由收到 `INSTALLER_CHAT_OK`；这验证入口与配置接线，测试服务不是真实外部模型。
- 重复安装前后对人设、模型配置、占位密钥、个人模块配置及额外个人文件进行哈希比较，五个文件保持一致；PATH 条目只有一份，安装锁与临时下载目录已清理。
- `bash -n scripts/bootstrap.sh` 通过；`python3 -m unittest discover -s tests -p test_bootstrap_installer.py -v` 七项通过，覆盖无终端拒绝、无副作用帮助、拒绝覆盖无关入口/个人目录、目录分离、工具失败后清理，以及两个发布脚本一致。

首次安装测试使用 `--no-setup --no-launch` 独立检查环境准备；交互模型设置及聊天接线另行检查。此处不宣称所有模型服务、消息平台或附加模块都已配置或重新验收。引导安装的个人目录是 `~/.nyairo`，手动教程的旧目录仍保留兼容。

## rc6 更新验收

本轮整包更新与失败恢复的实际结果见 `UPDATE_TEST_EVIDENCE.json`。rc5 隐私证据仍保留为 rc5 的结果，不作为 rc6 的新运行记录。没有宣称完整 Hermes 套件、Hermes 0.21.5 或真实生产机器升级已通过。

## E13 发布证据门禁（v0.2 自主生活，2026-10-11）

本节只判定 `v0.2-autonomous-life` 分支（本批起点 `e471241`）的**自主生活**能力。判定一律从实际跑过的证据出发，不按设计意图给分。**本节没有任何一项写 `DEPLOYED`**：没有真实生产切换，也没有对真实用户实例做过交付。

### 判定口径

| 等级 | 含义 |
| --- | --- |
| `IMPLEMENTED` | 代码存在、单元级检查通过，但没有走过真实 owner 链 |
| `TESTED_ISOLATED` | 隔离环境里用**真实 owner**（真实 Supply socket、真实 World 服务）跑过，但决策来自受控替身 |
| `E2E_VERIFIED` | 真实 owner 链 **且** 真实供应商（真实模型调用），没有替身 |
| `SOAK_VERIFIED` | 24 小时 / 7 天的真实时长已经跑完并留有记录 |
| `DEPLOYED` | 已经切换到真实生产实例（真实用户 profile / 真实服务） |

硬规则：

- 24 小时 / 7 天没有跑完 → **不得写 `SOAK_VERIFIED`**。
- 没有真实生产切换 → **不得写 `DEPLOYED`**。
- 只证明"机制"的（受控替身、受控时钟、隔离 harness）不得升级成 `E2E_VERIFIED`；单次文本差异不得当成长期人格证据。

### 逐能力判定

| 能力 | 等级 | 判定依据（实际证据） | 不得越级的部分 |
| --- | --- | --- | --- |
| 没有用户消息也能自主考虑 | `E2E_VERIFIED` | E13 走通：全新 profile 上由到期窗口触发一次考虑，全程无用户消息，决策来自真实模型（`gemini-3.8-flash-high`）。机制侧另有 E12.3 的 T01（`B8_ASSEMBLY PASS`，受控替身） | 只跑了**一个**窗口；没有长时间无人值守的统计 |
| 真实模型驱动的有界作品创作 | `E2E_VERIFIED` | E13 走通：owner 确认了 `artifact-version:7da6005463dd4e3d87e5ea20bdf8c3dd`（v2，parent 为 v1），正文是模型写的散文而非模板；步骤日志 `effect_state=APPLIED` | 只验证了一件作品、一次写入；没有跨作品/跨主题的样本 |
| 对同一件作品的持续编辑 | `E2E_VERIFIED` | B8-6 / B8-6A 长测：同一件作品由真实模型连续推进（v1→v5、v1→v3），每次都有 owner 回执；E13 走通也在同一件作品上把 v1 推进到 v2。受控替身版本见 T03（同一 Activity、`parent_version_id` = 上一版、checkpoint 前移） | 没有做跨天、跨重启的编辑统计 |
| 有界的 World MOVE | `E2E_VERIFIED` | T16（`run_world_move_e2e.sh`）：真实 World 服务 + 真 gateway，`home`→`nearby_street`、revision 1→2、重启后保持；不可达的 MOVE 被拒且世界不变；UNKNOWN 用原 execution_id 对账、不二次移动（`WORLD_MOVE_ACCEPTANCE PASS`） | 本批**没有**复跑 T16；E13 的隔离 profile 没有配 World（观测记 `UNAVAILABLE`），"自主循环里由真实模型选择一次 MOVE"没有证据 |
| 真实结果形成受控的生活经历 | `TESTED_ISOLATED` | E13 走通：真实提交在 M0 留下一条 `LIFE_VERIFIED_OUTCOME` 记录（正文由结构化字段拼成，不是手写）；三个不同性质的真实结果（成功提交 / 冲突拒绝 / 放弃）的准入见 B8-5 实验 B1（`accepted=1 / duplicate=0 / defer=0`） | **经历改变决策**没有证据：B8-5 实验 B2 的真实模型半边当时 `NOT_RUN`，本批没有复跑；`NO_CHANGE` 无法成为经历（AG-2 词表缺口） |
| 预算、静默、重启恢复 | `TESTED_ISOLATED` | T14（空窗口 `IDLE_NO_CANDIDATE`、`llm_calls=0`、无编造经历）、T15（预算耗尽 → `BLOCKED`，不是"她选择休息"）、T18（重启后开关/预算/事件游标保持）、T19（关闭后新决策 `DISABLED`，数据与投影仍可用）；E13 走通实测默认关（`DISABLED`、`model_calls 0`）与真实预算消耗（认知 1/2、动作 1/1） | 真实一整天的预算行为没有证据（长测未达 24 小时） |
| 只读生活投影与 Hermes 聊天并存 | `TESTED_ISOLATED` | E13 走通：只读投影真实渲染出"当前作品 + 最新已确认版本"（0 次模型调用）；T19 证明关闭自主开关后聊天投影与 status 仍工作；`tests/test_presence_integration.py` 另有投影不暴露 owner 权限与原始状态的检查 | 隔离环境里**没有**做真实 Telegram / 网关收发；投影与聊天同进程共存的端到端路径未验证。`tests/test_presence_integration.py` 属于当前失败的 `host-boundaries` 套件（原因见下） |

### 与本次判定一起读的限定

- 本次走通使用的是一次**真实供应商调用共 2 次**（1 次决策 + 1 次内容生成）。`counters.model_calls` 与每日认知计数只统计决策调用，见 `KNOWN_ISSUES.md` 的记录。
- 长测的 24 小时 / 7 天状态仍是 `SOAK_RUNNING_24H` / `SOAK_PENDING_7D`（观察起点 `2026-10-11T01:07:06+00:00`，本批结束约 24 分钟），因此**没有** `SOAK_VERIFIED`。修复前的故障发现记录不与修复后的时段拼接。
- `scripts/test.py` 的三个环境相关失败套件**保持 FAIL 并如实记录**（记录自 B8-6A 在同一提交 `e471241` 上的运行；本批只改文档，没有复跑，也没有据此宣称全绿）：`host-boundaries`（安装器/更新/文件系统安全测试）、`cognition-faults`（`vendor/hermes` 没有 pytest 虚拟环境）、`Hermes`（vendor 测试运行器）。没有为了"全绿"添加无条件跳过或重构安装器。
- 生产部署形态（systemd 单元）没有在真实硬件上验证；本机 systemd 为 `offline`。
- 旧供应商密钥曾在进程命令行上暴露过（B8-6 §7.2），代码路径已修（B8-6A §4），**旧密钥的轮换仍由账户持有人在供应商后台完成**，本批没有、也不能代替它。

### 不得声称（与上面的等级表配合阅读）

自主给用户发 Telegram 消息；执行任意 shell / 浏览器操作；自动付款或公开发布；完整的虚拟世界活动系统（`POSE` / `PICK_UP` / `PLACE` 未做）；科学验证过的长期自主人格；v0.2 已交付给所有用户。以上任何一条都**没有**证据，不得写入发布说明或对外材料。
